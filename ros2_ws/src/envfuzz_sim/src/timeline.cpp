#include <chrono>
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include <gz/math/Pose3.hh>
#include <gz/plugin/Register.hh>
#include <gz/sim/EntityComponentManager.hh>
#include <gz/sim/Model.hh>
#include <gz/sim/System.hh>
#include <gz/sim/World.hh>
#include <sdf/Element.hh>

namespace envfuzz
{
class Timeline final : public gz::sim::System,
                       public gz::sim::ISystemConfigure,
                       public gz::sim::ISystemPreUpdate
{
  public: void Configure(const gz::sim::Entity &entity,
                         const std::shared_ptr<const sdf::Element> &sdf,
                         gz::sim::EntityComponentManager &,
                         gz::sim::EventManager &) override
  {
    this->world = gz::sim::World(entity);
    auto config = sdf->Clone();
    if (!config->HasElement("move"))
      return;
    for (auto move = config->GetElement("move"); move; move = move->GetNextElement("move"))
    {
      this->moves.push_back({
        move->Get<unsigned int>("index"),
        static_cast<std::int64_t>(move->Get<unsigned long>("nanoseconds")),
        move->Get<std::string>("model"),
        move->Get<gz::math::Pose3d>("pose")
      });
    }
  }

  public: void PreUpdate(const gz::sim::UpdateInfo &info,
                         gz::sim::EntityComponentManager &ecm) override
  {
    if (info.paused)
      return;
    const auto now = std::chrono::duration_cast<std::chrono::nanoseconds>(info.simTime).count();
    while (this->next < this->moves.size() && this->moves[this->next].nanoseconds <= now)
    {
      const auto &move = this->moves[this->next++];
      const auto model = this->world.ModelByName(ecm, move.model);
      if (model == gz::sim::kNullEntity)
      {
        std::cerr << "ENVFUZZ_EVENT_ERROR " << move.index << std::endl;
        continue;
      }
      gz::sim::Model(model).SetWorldPoseCmd(ecm, move.pose);
      std::cout << "ENVFUZZ_EVENT " << move.index << " " << now << std::endl;
    }
  }

  private: struct Move
  {
    unsigned int index;
    std::int64_t nanoseconds;
    std::string model;
    gz::math::Pose3d pose;
  };

  private: gz::sim::World world;
  private: std::vector<Move> moves;
  private: std::size_t next = 0;
};
}

GZ_ADD_PLUGIN(envfuzz::Timeline, gz::sim::System,
              envfuzz::Timeline::ISystemConfigure, envfuzz::Timeline::ISystemPreUpdate)
