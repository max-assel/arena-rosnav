from typing import Any, Callable, Dict, List

import numpy as np
import rospy

from .constants import REWARD_CONSTANTS
from .utils import load_rew_fnc


class RewardFunction:
    def __init__(
        self,
        rew_func_name: str,
        holonomic: bool,
        robot_radius: float,
        goal_radius: float,
        safe_dist: float,
    ):
        self._rew_func_name = rew_func_name
        self._holonomic = holonomic
        self._robot_radius = robot_radius
        self._safe_dist = safe_dist
        self._goal_radius = goal_radius

        self._curr_dist_to_path: float = None
        self._safe_dist_breached: bool = None

        self._curr_reward = 0
        self._info = {}

        self._reward_units: List[
            Callable[[Dict[str, Any]], None]
        ] = RewardFunction._setup_reward_function(self, self._rew_func_name)

    @staticmethod
    def _setup_reward_function(
        rew_fnc_cls: "RewardFunction", rew_fnc_name: str
    ) -> List[Callable[[Dict[str, Any]], None]]:
        import rl_utils.utils.rewards as rew_pkg

        rew_fnc_dict = load_rew_fnc(rew_fnc_name)
        return [
            rew_pkg.RewardUnitFactory.instantiate(unit_name)(
                reward_function=rew_fnc_cls, **kwargs
            )
            for unit_name, kwargs in rew_fnc_dict.items()
        ]

    def add_reward(self, value: float):
        self._curr_reward += value

    def add_info(self, info: dict):
        self._info.update(info)

    def _reset(self):
        self._curr_reward = 0
        self._info = {}

    def reset(self):
        self.goal_radius = rospy.get_param("/goal_radius", 0.3)
        self._curr_dist_to_path = None

        for reward_unit in self._reward_units:
            reward_unit.reset()

    def calculate_reward(self, *args, **kwargs):
        self._reset()
        self.set_safe_dist_breached(kwargs["laser_scan"])

        for reward_unit in self._reward_units:
            if self.safe_dist_breached and not reward_unit.on_safe_dist_violation:
                continue
            reward_unit(**kwargs)

    def get_reward(self, *args, **kwargs):
        self.calculate_reward(**kwargs)
        return self._curr_reward, self._info

    @property
    def holonomic(self) -> float:
        return self._holonomic

    @property
    def robot_radius(self) -> float:
        return self._robot_radius

    @property
    def goal_radius(self) -> float:
        return self._goal_radius

    @goal_radius.setter
    def goal_radius(self, value) -> None:
        if value < REWARD_CONSTANTS.MIN_GOAL_RADIUS:
            raise ValueError(
                f"Goal radius smaller than {REWARD_CONSTANTS.MIN_GOAL_RADIUS}"
            )
        self._goal_radius = value

    @property
    def curr_dist_to_path(self) -> float:
        return self._curr_dist_to_path

    @curr_dist_to_path.setter
    def curr_dist_to_path(self, value: float) -> None:
        self._curr_dist_to_path = value

    @property
    def safe_dist_breached(self) -> bool:
        return self._safe_dist_breached

    def set_safe_dist_breached(self, laser_scan: np.ndarray) -> None:
        self._safe_dist_breached = laser_scan.min() <= self._safe_dist

    def __repr__(self) -> str:
        format_string = self.__class__.__name__ + "("
        for t in self._reward_units:
            format_string += "\n"
            format_string += f"    {t}"
        format_string += "\n)"
        return format_string
