"""Local web review GUI for campaign failure analysis."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import shutil
import subprocess
import threading
import uuid
import webbrowser

from pydantic import ValidationError

from .manual_review import (
    ManualReview,
    export_failure_candidates,
    load_manual_reviews,
    upsert_manual_review,
)


class ReplayJobs:
    def __init__(self, campaign: Path, backend: str, image: str, video_image: str = "envfuzz:jazzy-video"):
        self.campaign = campaign.resolve()
        self.backend = backend
        self.image = image
        self.video_image = video_image
        self.jobs: dict[str, dict] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        self.container_names: dict[str, str] = {}
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="envfuzz-replay")

    def create(self, case: int, attempt: int, bundle: str, repeats: int, video: bool) -> dict:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "case": case,
            "attempt": attempt,
            "bundle": bundle,
            "repeats": repeats,
            "video": video,
            "state": "queued",
            "results": [],
            "error": None,
        }
        with self.lock:
            self.jobs[job_id] = job
        self.executor.submit(self.run, job_id)
        return job.copy()

    def run(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            if job["state"] == "cancelled":
                return
            job["state"] = "running"

        source = (self.campaign / job["bundle"]).resolve()
        if not _within(self.campaign, source):
            self._finish(job_id, "failed", "Replay source is outside the campaign.")
            return

        for repeat in range(1, job["repeats"] + 1):
            with self.lock:
                if job["state"] == "cancelled":
                    return
            output = (
                self.campaign
                / "manual-reviews"
                / f"case-{job['case']:06d}"
                / f"{job_id}-repeat-{repeat:03d}"
            )
            bundle = output / "bundle"
            try:
                result = self._replay(source, bundle, job["video"], job_id)
            except (OSError, subprocess.SubprocessError) as error:
                with self.lock:
                    if job["state"] == "cancelled":
                        return
                self._finish(job_id, "failed", str(error))
                return
            with self.lock:
                if job["state"] == "cancelled":
                    return
                job["results"].append({
                    "repeat": repeat,
                    "bundle": bundle.relative_to(self.campaign).as_posix(),
                    "status": result,
                })
                if result == "launch_failed":
                    job["error"] = "Replay launch failed and produced no result bundle."
                    job["state"] = "failed"
                    return
        self._finish(job_id, "completed", None)

    def _replay(self, source: Path, output: Path, video: bool, job_id: str) -> str:
        args = ["replay", str(source), "--output", str(output), "--replay-origin", str(source)]
        if video:
            args.append("--video")

        if self.backend == "docker" or video:
            docker = shutil.which("docker")
            if docker is None:
                raise OSError("Docker is required for isolated video replay.")
            mounted_source = "/workspace/envfuzz/review/" + source.relative_to(self.campaign).as_posix()
            mounted_output = "/workspace/envfuzz/review/manual-reviews"
            relative_output = str(output.relative_to(self.campaign)).removeprefix("manual-reviews/")
            container_output = mounted_output + "/" + relative_output
            container_name = f"envfuzz-replay-{job_id}"
            selected_image = self.video_image if video else self.image
            command = [
                docker, "run", "--rm", "--name", container_name,
                "-v", f"{self.campaign}:/workspace/envfuzz/review",
                selected_image,
                *args[:1],
                mounted_source,
                "--output",
                container_output,
                "--replay-origin",
                str(source),
            ]
            if video:
                command.append("--video")
        else:
            executable = shutil.which("envfuzz")
            if executable is None:
                raise OSError("envfuzz executable is unavailable.")
            command = [executable, *args]

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        with self.lock:
            cancelled = self.jobs[job_id]["state"] == "cancelled"
            self.processes[job_id] = process
            if self.backend == "docker" or video:
                self.container_names[job_id] = container_name
        if cancelled:
            if self.backend == "docker" or video:
                subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=5, check=False)
            process.terminate()
        config = json.loads((source / "run.json").read_text(encoding="utf-8"))
        try:
            stdout, stderr = process.communicate(timeout=float(config.get("watchdog", 300)) + 30)
        except subprocess.TimeoutExpired:
            if self.backend == "docker" or video:
                subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=5, check=False)
            process.kill()
            stdout, stderr = process.communicate()
            raise TimeoutError("Replay exceeded its wall-clock watchdog and was stopped.")
        with self.lock:
            self.processes.pop(job_id, None)
            self.container_names.pop(job_id, None)
        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if completed.returncode not in (0, 2):
            raise OSError(completed.stderr.strip() or completed.stdout.strip() or "Replay launch failed.")

        result_path = output / "result.json"
        if not result_path.is_file():
            return "launch_failed"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        return result.get("status", "unknown")

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs[job_id]
            if job["state"] in {"queued", "running"}:
                job["state"] = "cancelled"
                job["error"] = "Cancelled by reviewer."
            process = self.processes.get(job_id)
            container_name = self.container_names.get(job_id)
        if process is not None:
            if container_name and shutil.which("docker"):
                subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=5, check=False)
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if container_name and process is None and shutil.which("docker"):
            subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=5, check=False)
        return job.copy()

    def get(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs[job_id]
            return json.loads(json.dumps(job))

    def all(self) -> list[dict]:
        with self.lock:
            return [json.loads(json.dumps(job)) for job in self.jobs.values()]

    def _finish(self, job_id: str, state: str, error: str | None) -> None:
        with self.lock:
            job = self.jobs[job_id]
            if job["state"] != "cancelled":
                job["state"] = state
                job["error"] = error


def _within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def candidate_report(campaign: Path) -> dict:
    campaign = campaign.resolve()
    export_failure_candidates(
        campaign,
        campaign / "review-candidates.json",
        campaign / "review-candidates.csv",
    )
    return json.loads((campaign / "review-candidates.json").read_text(encoding="utf-8"))


def candidate_detail(campaign: Path, case: int, attempt: int) -> dict:
    report = candidate_report(campaign)
    candidate = next(
        (row for row in report["candidates"] if row["case"] == case and row["attempt"] == attempt),
        None,
    )
    if candidate is None:
        raise KeyError("Candidate not found")

    bundle = (campaign / candidate["bundle"]).resolve()
    if not _within(campaign, bundle):
        raise ValueError("Bundle is outside the campaign")

    def read_json(name: str) -> dict | list | None:
        path = bundle / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    review = load_manual_reviews(campaign).get(case, {}).get(attempt)
    result = read_json("result.json")
    metrics = read_json("metrics.json")
    scenario = read_json("scenario.json")
    telemetry = []
    telemetry_path = bundle / "telemetry.jsonl"
    if telemetry_path.is_file():
        telemetry = [
            json.loads(line)
            for line in telemetry_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    video_metadata_path = bundle / "video.json"
    video_metadata = json.loads(video_metadata_path.read_text(encoding="utf-8")) if video_metadata_path.is_file() else None
    return {
        "candidate": candidate,
        "review": review.model_dump() if review else None,
        "result": result,
        "metrics": metrics,
        "scenario": scenario,
        "telemetry": telemetry,
        "video": video_metadata,
        "artifacts": {
            "diagnostics": (bundle / "diagnostics.html").is_file(),
            "trajectory": (bundle / "trajectory.svg").is_file(),
            "logs": (bundle / "logs.txt").is_file(),
            "map_alignment": (bundle / "map-alignment.json").is_file(),
            "video": (bundle / "episode.mp4").is_file(),
            "video_metadata": video_metadata_path.is_file(),
        },
    }


def serve_review(
    campaign: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    backend: str = "local",
    image: str = "envfuzz:jazzy",
    video_image: str = "envfuzz:jazzy-video",
) -> None:
    campaign = campaign.resolve()
    jobs = ReplayJobs(campaign, backend, image, video_image)
    handler = create_handler(campaign, jobs)
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"EnvFuzz review GUI: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        jobs.executor.shutdown(wait=False)


def create_handler(campaign: Path, jobs: ReplayJobs):
    campaign = campaign.resolve()

    class ReviewHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def _json(self, status: int, payload: object) -> None:
            data = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _html(self, status: int, page: str) -> None:
            data = page.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._html(200, review_page())
            elif parsed.path == "/api/candidates":
                try:
                    query = parse_qs(parsed.query)
                    report = candidate_report(campaign)
                    candidates = report["candidates"]
                    filters = {
                        "layout": query.get("layout", [None])[0],
                        "strategy": query.get("strategy", [None])[0],
                        "status": query.get("status", [None])[0],
                        "review_status": query.get("review_status", [None])[0],
                        "failure_category": query.get("failure_category", [None])[0],
                        "confidence": query.get("confidence", [None])[0],
                        "reproducibility": query.get("reproducibility", [None])[0],
                    }
                    candidates = [
                        row for row in candidates
                        if all(value is None or row[key] == value for key, value in filters.items())
                    ]
                    self._json(200, {"campaign": campaign.name, "candidates": candidates})
                except (OSError, ValueError, KeyError) as error:
                    self._json(500, {"error": str(error)})
            elif parsed.path.startswith("/api/cases/"):
                pieces = parsed.path.strip("/").split("/")
                if len(pieces) == 5:
                    try:
                        case = int(pieces[2])
                        attempt = int(pieces[4])
                        self._json(200, candidate_detail(campaign, case, attempt))
                    except (ValueError, KeyError) as error:
                        self._json(404, {"error": str(error)})
                else:
                    self._json(404, {"error": "Case not found"})
            elif parsed.path == "/api/jobs":
                self._json(200, {"jobs": jobs.all()})
            elif parsed.path.startswith("/api/jobs/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                try:
                    self._json(200, jobs.get(job_id))
                except KeyError:
                    self._json(404, {"error": "Job not found"})
            elif parsed.path == "/artifact":
                query = parse_qs(parsed.query)
                relative = query.get("path", [None])[0]
                if not relative:
                    self._json(400, {"error": "path is required"})
                    return
                target = (campaign / relative).resolve()
                if not _within(campaign, target) or not target.is_file():
                    self._json(404, {"error": "Artifact not found"})
                    return
                content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json(404, {"error": "Not found"})

        def do_POST(self) -> None:
            from urllib.parse import urlparse

            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except json.JSONDecodeError:
                self._json(400, {"error": "Invalid JSON"})
                return

            if parsed.path.startswith("/api/cases/") and parsed.path.endswith("/review"):
                pieces = parsed.path.strip("/").split("/")
                if len(pieces) != 6:
                    self._json(400, {"error": "Invalid review path"})
                    return
                try:
                    case = int(pieces[2])
                    attempt = int(pieces[4])
                    candidate = candidate_detail(campaign, case, attempt)["candidate"]
                    review = ManualReview(
                        campaign=campaign.name,
                        case=case,
                        attempt=attempt,
                        bundle=candidate["bundle"],
                        observed_status=candidate["status"],
                        observed_classification=candidate["classification"],
                        review_status=body.get("review_status", "reviewing"),
                        failure_category=body.get("failure_category", "unknown"),
                        root_cause_category=body.get("root_cause_category", "unknown"),
                        confidence=body.get("confidence") or None,
                        reproducibility=body.get("reproducibility", "unknown"),
                        evidence_summary=body.get("evidence_summary", ""),
                        presentation_candidate=bool(body.get("presentation_candidate", False)),
                        video=body.get("video") or None,
                    )
                    upsert_manual_review(campaign, review)
                    self._json(200, {"review": review.model_dump()})
                except (ValidationError, ValueError, KeyError) as error:
                    self._json(400, {"error": str(error)})
            elif parsed.path == "/api/replays":
                try:
                    case = int(body["case"])
                    attempt = int(body["attempt"])
                    bundle = str(body["bundle"])
                    repeats = int(body.get("repeats", 1))
                    video = bool(body.get("video", False))
                    if repeats < 1 or repeats > 3:
                        raise ValueError("replays must be between 1 and 3")
                    job = jobs.create(case, attempt, bundle, repeats, video)
                    self._json(202, {"job": job})
                except (KeyError, ValueError) as error:
                    self._json(400, {"error": str(error)})
            elif parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
                job_id = parsed.path.split("/")[-2]
                try:
                    self._json(200, {"job": jobs.cancel(job_id)})
                except KeyError:
                    self._json(404, {"error": "Job not found"})
            else:
                self._json(404, {"error": "Not found"})

    return ReviewHandler


def review_page() -> str:
    return """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>EnvFuzz failure review</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;color:#101828;background:#f8fafc}
header{padding:16px 24px;background:#101828;color:white}
main{display:grid;grid-template-columns:minmax(280px,420px) 1fr;height:calc(100vh - 62px)}
#queue{overflow:auto;padding:16px;border-right:1px solid #d0d5dd}
#detail{overflow:auto;padding:24px;background:white}
select,input,textarea,button{font:inherit;margin:4px 4px 4px 0;padding:8px}
.candidate{border:1px solid #d0d5dd;border-radius:8px;padding:12px;margin:8px 0;background:white;cursor:pointer}
.candidate:hover{border-color:#175cd3}
.candidate.selected{border:2px solid #175cd3}
.status{display:inline-block;padding:2px 8px;border-radius:999px;background:#fef0c7;color:#93370d}
pre{max-height:280px;overflow:auto;background:#f2f4f7;padding:12px}
label{display:block;margin-top:8px}
</style>
<header><strong>EnvFuzz campaign review</strong></header>
<main>
<section id="queue">
<div>
<select id="layout"><option value="">All layouts</option><option>apartment</option><option>corridor</option><option>open_room</option></select>
<select id="strategy"><option value="">All strategies</option><option>uniform</option><option>stratified</option><option>coverage</option></select>
<select id="status"><option value="">All outcomes</option><option>timeout</option><option>mission_failed</option></select>
<select id="review_status"><option value="">All review states</option><option>unreviewed</option><option>reviewing</option><option>confirmed</option><option>rejected</option></select>
<select id="failure_category"><option value="">All categories</option><option>unknown</option><option>planning</option><option>localization</option><option>perception</option><option>control</option><option>recovery</option><option>infrastructure</option><option>oracle</option><option>scenario</option></select>
<select id="confidence"><option value="">All confidence</option><option>low</option><option>medium</option><option>high</option></select>
<select id="reproducibility"><option value="">Any reproducibility</option><option>unknown</option><option>reproducible</option><option>not_reproducible</option></select>
</div>
<div id="candidates">Loading...</div>
</section>
<section id="detail"><h2>Select a candidate</h2></section>
</main>
<script>
const escapeHtml=(s)=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function loadCandidates(){
 const element=document.getElementById("candidates");
 element.innerHTML="Loading...";
 const filters=["layout","strategy","status","review_status","failure_category","confidence","reproducibility"].map(id=>{const v=document.getElementById(id).value;return v?`${id}=${encodeURIComponent(v)}`:""});
 try{
  const response=await fetch("/api/candidates"+(filters.filter(Boolean).length?"?"+filters.filter(Boolean).join("&"):""));
  if(!response.ok) throw new Error(`Candidate request failed (${response.status})`);
  const data=await response.json();
  const candidates=Array.isArray(data.candidates)?data.candidates:[];
  element.innerHTML=candidates.map(c=>`<div class="candidate" data-case="${c.case}" data-attempt="${c.attempt}">
  <strong>Case ${c.case}, attempt ${c.attempt}</strong><br>
  <span class="status">${escapeHtml(c.status)}</span> ${escapeHtml(c.layout)} / ${escapeHtml(c.strategy)}
  </div>`).join("")||"No matching candidates";
  document.querySelectorAll(".candidate").forEach(el=>el.onclick=()=>openCandidate(+el.dataset.case,+el.dataset.attempt));
 }catch(error){
  element.innerHTML=`<div>Could not load candidates: ${escapeHtml(error.message||String(error))}</div>`;
 }
}
async function openCandidate(caseId,attempt){
 document.querySelectorAll(".candidate").forEach(el=>el.classList.toggle("selected",+el.dataset.case===caseId&&+el.dataset.attempt===attempt));
 const d=await fetch(`/api/cases/${caseId}/attempts/${attempt}`).then(r=>r.json());
 if(d.error){document.getElementById("detail").innerHTML=escapeHtml(d.error);return;}
 const c=d.candidate,r=d.review||{};
 const artifact=(name,label)=>d.artifacts[name]?`<a href="/artifact?path=${encodeURIComponent(c.bundle+"/"+(name==="diagnostics"?"diagnostics.html":name==="trajectory"?"trajectory.svg":name==="logs"?"logs.txt":name==="video_metadata"?"video.json":"video.json"))}" target="_blank">${label}</a>`:"";
 const videoArtifact=d.artifacts.video?`<p><video controls style="max-width:100%"><source src="/artifact?path=${encodeURIComponent(c.bundle+"/episode.mp4")}" type="video/mp4"></video></p>`:"";
 const videoStatus=d.video?`<p>Video: ${escapeHtml(d.video.status)}${d.video.reason?" — "+escapeHtml(d.video.reason):""} ${artifact("video_metadata","metadata")}</p>`:`<p>Video: not recorded</p>`;
 document.getElementById("detail").innerHTML=`<h2>Case ${c.case}, attempt ${c.attempt}</h2>
 <p><span class="status">${escapeHtml(c.status)}</span> ${escapeHtml(c.layout)} / ${escapeHtml(c.strategy)}</p>
 <p>${artifact("diagnostics","Diagnostics")} ${artifact("trajectory","Trajectory")} ${artifact("logs","Logs")}</p>
 ${videoArtifact}
 ${videoStatus}
 <h3>Result</h3><pre>${escapeHtml(JSON.stringify(d.result,null,2))}</pre>
 <h3>Metrics</h3><pre>${escapeHtml(JSON.stringify(d.metrics,null,2))}</pre>
 <h3>Scenario</h3><pre>${escapeHtml(JSON.stringify(d.scenario,null,2))}</pre>
 <h3>Replay</h3>
 <label>Repeats <input id="repeats" type="number" min="1" max="3" value="1"></label>
 <label><input id="video" type="checkbox"> Request video</label>
 <button id="start-replay-button" data-case="${c.case}" data-attempt="${c.attempt}" data-bundle="${escapeHtml(c.bundle)}">Replay</button>
 <div id="jobs"></div>
 <h3>Manual review</h3>
 <form id="review" onsubmit="saveReview(event,${c.case},${c.attempt})">
 <label>Review status <select id="review_status"><option>reviewing</option><option>confirmed</option><option>rejected</option><option>unreviewed</option></select></label>
 <label>Failure category <select id="failure_category"><option>unknown</option><option>planning</option><option>localization</option><option>perception</option><option>control</option><option>recovery</option><option>infrastructure</option><option>oracle</option><option>scenario</option></select></label>
 <label>Root cause <select id="root_cause_category"><option>unknown</option><option>blocked_route</option><option>localization_drift</option><option>sensor_degradation</option><option>stale_map</option><option>stuck_recovery</option><option>startup_stall</option><option>resource_contention</option><option>incorrect_result_classification</option></select></label>
 <label>Confidence <select id="confidence"><option value="">-</option><option>low</option><option>medium</option><option>high</option></select></label>
 <label>Reproducibility <select id="reproducibility"><option>unknown</option><option>reproducible</option><option>not_reproducible</option></select></label>
 <label>Evidence summary<textarea id="evidence_summary" rows="4"></textarea></label>
 <label><input id="presentation_candidate" type="checkbox"> Presentation candidate</label>
 <button type="submit">Save review</button>
 </form>`;
 for(const id of ["review_status","failure_category","root_cause_category","confidence","reproducibility","evidence_summary"]) document.getElementById(id).value=r[id]||document.getElementById(id).value;
 document.getElementById("presentation_candidate").checked=!!r.presentation_candidate;
 const replayButton=document.getElementById("start-replay-button");
 replayButton.onclick=()=>startReplay(
   Number(replayButton.dataset.case),
   Number(replayButton.dataset.attempt),
   replayButton.dataset.bundle
 );
 refreshJobs();
}
async function saveReview(event,caseId,attempt){
 event.preventDefault();
 const body={};
 for(const id of ["review_status","failure_category","root_cause_category","confidence","reproducibility","evidence_summary"]) body[id]=document.getElementById(id).value;
 body.presentation_candidate=document.getElementById("presentation_candidate").checked;
 const response=await fetch(`/api/cases/${caseId}/attempts/${attempt}/review`,{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const data=await response.json();alert(data.error||"Review saved");
 loadCandidates();
}
async function startReplay(caseId,attempt,bundle){
 const jobs=document.getElementById("jobs");
 if(jobs) jobs.innerHTML="<div><b>Starting replay...</b></div>";
 const body={case:caseId,attempt,bundle,repeats:+document.getElementById("repeats").value,video:document.getElementById("video").checked};
 try{
  const response=await fetch("/api/replays",{method:"POST",headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok){
   if(jobs) jobs.innerHTML=`<div><b>Replay request failed:</b> ${escapeHtml(data.error||"Unknown error")}</div>`;
   alert(data.error||"Replay could not be started");
   return;
  }
  if(jobs){
   jobs.innerHTML=`<div><b>Replay ${escapeHtml(data.job.id)}</b>: ${escapeHtml(data.job.state)}</div>`;
  }
  await refreshJobs();
 }catch(error){
  if(jobs) jobs.innerHTML=`<div><b>Replay request failed:</b> ${escapeHtml(error.message||String(error))}</div>`;
  alert(error.message||"Replay could not be started");
 }
}
async function refreshJobs(){
 const el=document.getElementById("jobs");if(!el)return;
 try{
  const response=await fetch("/api/jobs");
  const data=await response.json();
  el.innerHTML=data.jobs.map(j=>`<div><b>Replay ${j.id}</b>: ${escapeHtml(j.state)} ${j.error?"- "+escapeHtml(j.error):""}<br>${j.results.map(x=>`${x.repeat}: ${escapeHtml(x.status)} <a href="/artifact?path=${encodeURIComponent(x.bundle+"/result.json")}" target="_blank">result</a> ${j.video?`<a href="/artifact?path=${encodeURIComponent(x.bundle+"/video.json")}" target="_blank">video status</a> <a href="/artifact?path=${encodeURIComponent(x.bundle+"/episode.mp4")}" target="_blank">video</a>`:""}`).join("<br>")}</div>`).join("")||"No replay jobs";
 }catch(error){
  el.innerHTML=`<div><b>Could not load replay jobs:</b> ${escapeHtml(error.message||String(error))}</div>`;
 }
}
["layout","strategy","status","review_status","failure_category","confidence","reproducibility"].forEach(id=>document.getElementById(id).onchange=loadCandidates);
loadCandidates();setInterval(refreshJobs,2000);
</script>
</html>"""
