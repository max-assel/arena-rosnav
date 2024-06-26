import itertools
from math import floor
from typing import Collection, List, Optional, Tuple
import typing
import numpy as np
import scipy.signal
import rospy
from task_generator.constants import Config

from task_generator.manager.utils import World, WorldEntities, WorldMap, WorldObstacleConfiguration, WorldOccupancy, WorldWalls, configurations_to_obstacles, occupancy_to_walls
from task_generator.shared import Position, PositionRadius


class WorldManager:
    """
    The map manager manages the static map
    and is used to get new goal, robot and
    obstacle positions.
    """

    class Bounds(typing.NamedTuple):
        min_x: float = float("-inf")
        min_y: float = float("-inf")
        max_x: float = float("inf")
        max_y: float = float("inf")

    def __init__(self, world_map: WorldMap, world_obstacles: Optional[Collection[WorldObstacleConfiguration]] = None):
        self._classic_forbidden_zones = []
        self.update_world(world_map=world_map, world_obstacles=world_obstacles)

    @property
    def world(self) -> World:
        return self._world

    @property
    def _shape(self) -> Tuple[int, int]:
        return self._world.map.shape[0], self._world.map.shape[1]

    @property
    def origin(self) -> Position:
        return self._world.map.origin

    @property
    def resolution(self) -> float:
        return self._world.map.resolution

    @property
    def walls(self) -> WorldWalls:
        return self._world.entities.walls

    def update_world(
        self,
        world_map: WorldMap,
        world_obstacles: Optional[Collection[WorldObstacleConfiguration]] = None
    ):

        if world_obstacles is None:
            """this is OK because maps may not have preset entities"""
            world_obstacles = list()

        walls = occupancy_to_walls(
            occupancy_grid=world_map.occupancy._walls.grid,
            transform=world_map.tf_grid2pos
        )

        obstacles = configurations_to_obstacles(
            configurations=world_obstacles
        )

        entities = WorldEntities(
            obstacles=obstacles,
            walls=walls
        )

        self._world = World(
            entities=entities,
            map=world_map
        )

        for obstacle in self.world.entities.obstacles:
            self.world.map.occupancy.obstacle_occupy(
                *self.world.map.tf_posr2rect(
                    PositionRadius(
                        obstacle.position.x,
                        obstacle.position.y,
                        1   # TODO actual radius
                    )
                )
            )  

        self._garbage_offset = 0

    def forbid(self, forbidden_zones: List[PositionRadius]):
        for zone in forbidden_zones:
            self.world.map.occupancy.forbidden_occupy(*self.world.map.tf_posr2rect(zone))

    def forbid_clear(self):
        self._world.map.occupancy.forbidden_clear()

    def garbage_positions(self, n: int, offset: Optional[int] = None) -> List[Position]:
        """
        Return garbage positions outside the map.
        If you get spawned here, you should feel ashamed of yourself.

        Args:
            n: number of positions to generate
            offset: (optional) offset for consecutive calls

        Returns:
            List[Position]
        """

        if offset is None:
            offset = self._garbage_offset
        else:
            self._garbage_offset += n

        return [
            self._world.map.tf_grid2pos(
                (
                    (-1-floor(i/5)) * int(self._shape[1]/5),
                    int((i % 5) * self._shape[0]/5)
                )
            ) for i in range(offset, n+offset)]

    _world: World
    _garbage_offset: int
    _classic_forbidden_zones: List[PositionRadius]

    def _classic_random_pos_on_map(self, safe_dist: float, forbid: bool = True, forbidden_zones: Optional[List[PositionRadius]] = None) -> Position:
        """
        This function is used by the robot manager and
        obstacles manager to get new positions for both
        robot and obstalces.
        The function will choose a position at random
        and then validate the position. If the position
        is not valid a new position is chosen. When
        no valid position is found after 100 retries
        an error is thrown.
        Args:
            safe_dist: minimal distance to the next
                obstacles for calculated positons
            forbid: add returned waypoint to forbidden zones
            forbidden_zones: Array of (x, y, radius),
                describing circles on the map. New
                position should not lie on forbidden
                zones e.g. the circles.
                x, y and radius are in meters
        Returns:
            A tuple with three elements: x, y, theta
        """
        # safe_dist is in meters so at first calc safe dist to distance on
        # map -> resolution of map is m / cell -> safe_dist in cells is
        # safe_dist / resolution

        import math

        def is_pos_valid(x: float, y: float, safe_dist: float, forbidden_zones: List[PositionRadius]):
            """
            @safe_dist: minimal distance to the next obstacles for calculated positions
            """
            for p in forbidden_zones:
                # euklidian distance to the forbidden zone
                dist = math.floor(np.linalg.norm(
                    np.array([x, y]) - np.array([p.x, p.y]))) - p.radius

                if dist <= safe_dist:
                    return False

            return True

        safe_dist_in_cells = math.ceil(
            safe_dist / self.world.map.resolution) + 1

        forbidden_zones_in_cells: List[PositionRadius] = [
            PositionRadius(
                math.ceil(point[0] / self.world.map.resolution),
                math.ceil(point[1] / self.world.map.resolution),
                math.ceil(point[2] / self.world.map.resolution)
            )
            for point in self._classic_forbidden_zones + (forbidden_zones if forbidden_zones is not None else [])
        ]

        # Now get index of all cells were dist is > safe_dist_in_cells
        possible_cells: List[Tuple[np.intp, np.intp]] = np.array(
            np.where(self.world.map.occupancy.grid > safe_dist_in_cells)).transpose().tolist()

        # return (random.randint(1,6), random.randint(1, 9), 0)
        assert len(possible_cells) > 0, "No cells available"

        # The position should not lie in the forbidden zones and keep the safe
        # dist to these zones as well. We could remove all cells here but since
        # we only need one position and the amount of cells can get very high
        # we just pick positions at random and check if the distance to all
        # forbidden zones is high enough

        while len(possible_cells) > 0:

            # Select a random cell
            x, y = possible_cells.pop(Config.General.RNG.integers(len(possible_cells)))

            # Check if valid
            if is_pos_valid(float(x), float(y), safe_dist_in_cells, forbidden_zones_in_cells):
                break

        else:
            raise Exception("can't find any non-occupied spaces")

        point = PositionRadius(
            float(np.round(float(x) * self.world.map.resolution +
                  self.world.map.origin.x, 3)),
            float(np.round(float(y) * self.world.map.resolution +
                  self.world.map.origin.y, 3)),
            safe_dist
        )

        if forbid:
            self._classic_forbidden_zones.append(point)

        return Position(point.x, point.y)

    def positions_on_map(self,
        n: int,
        safe_dist: float,
        forbidden_zones: Optional[List[PositionRadius]] = None,
        forbid: bool = True,
        bounds: typing.Optional[Bounds] = None
    ) -> List[Position]:
        """
        This function is used by the robot manager and
        obstacles manager to get new positions for both
        robot and obstalces.
        The function will choose a position at random
        and then validate the position. If the position
        is not valid a new position is chosen. When
        no valid position is found after 100 retries
        an error is thrown.
        Args:
            safe_dist: minimal distance to the next obstacles for calculated positons
            forbid: add returned waypoint to forbidden zones
            forbidden_zones: Array of (x, y, radius),
                describing circles on the map. New
                position should not lie on forbidden
                zones e.g. the circles.
                x, y and radius are in meters
            bounds: rectangle to restrict map to
        """
        # safe_dist is in meters so at first calc safe dist to distance on
        # map -> resolution of map is m / cell -> safe_dist in cells is
        # safe_dist / resolution

        if forbidden_zones is None:
            forbidden_zones = []

        fork = self._world.map.occupancy.fork()

        points: List[Position] = []

        if n < 0 and bounds is None:  # TODO profile when this is faster
            for _ in range(n):
                pos = self._classic_random_pos_on_map(
                    safe_dist=safe_dist, forbidden_zones=forbidden_zones)
                posr = PositionRadius(*pos, safe_dist)
                fork.occupy(*self.world.map.tf_posr2rect(posr))
                forbidden_zones.append(posr)
                points.append(pos)
                
            return points

        else:
            max_depth = 10

            if bounds is not None:
                fork.occupy(
                    lo = self.world.map.tf_pos2grid(Position(bounds.min_x, bounds.min_y)),
                    hi = self.world.map.tf_pos2grid(Position(bounds.max_x, bounds.max_y)),
                    inv = True
                )

            for zone in forbidden_zones:
                fork.occupy(
                    *self.world.map.tf_posr2rect(
                        PositionRadius(
                            zone.x,
                            zone.y,
                            zone.radius / self.resolution
                        )
                    )
                )

            min_dist: float = safe_dist / self.resolution
            available_positions = self._occupancy_to_available(
                occupancy=fork.grid, safe_dist=min_dist)

            def sample(target: int) -> Collection[Position]:

                all_banned: np.ndarray = np.zeros((target, 2))
                banned_index: int = 0

                result: List[Position] = list()
                depth: int = 0

                to_produce = target

                try:
                    while depth < max_depth:

                        if to_produce > len(available_positions):
                            raise RuntimeError()

                        candidates = available_positions[Config.General.RNG.choice(
                            len(available_positions), to_produce, replace=False), :]

                        for candidate in candidates:

                            banned = all_banned[:banned_index, :]

                            if np.any(np.linalg.norm(banned - candidate, axis=1) < min_dist):
                                continue

                            all_banned[banned_index] = candidate
                            banned_index += 1

                            fork.occupy(
                                (candidate-min_dist),
                                (candidate+min_dist)
                            )
                            
                            result.append(self._world.map.tf_grid2pos(
                                (candidate[0], candidate[1])))

                        to_produce = target - len(result)
                        if to_produce <= 0:
                            break

                        depth += 1

                    else:
                        raise RuntimeError(
                            f"Failed to find free position after {depth} tries")

                except RuntimeError:
                    result += self.garbage_positions(to_produce)
                    rospy.logerr(f"Couldn't find enough empty cells for {to_produce} requests")
                
                finally:
                    return result

            points = list(sample(n))

        if forbid:
            fork.commit()

        return points

    def positions_in_zones(self, n: int, safe_dist: float, zones: List[List[List[int]]], forbidden_zones: Optional[List[PositionRadius]] = None, forbid: bool = True) -> List[Position]:
            """
            This function is used in tm_zones (zones.py) to get
            new position and waypoints for dynamic obstacles.
            The function will choose a position in a specified zone list
            at random and then validate the position. If the position
            is not valid a new position is chosen. When
            no valid position is found after 100 retries
            an error is thrown.
            Args:
                n: number of points to be generated
                safe_dist: minimal distance to the next
                    obstacles for calculated positons
                zones: List of zones (which is a list of corner points of a zone)
                    in which to place the points.
                forbidden_zones: Array of (x, y, radius),
                    describing circles on the map. New
                    position should not lie on forbidden
                    zones e.g. the circles.
                    x, y and radius are in meters
                forbid: add returned waypoint to forbidden zones

            """
            # safe_dist is in meters so at first calc safe dist to distance on
            # map -> resolution of map is m / cell -> safe_dist in cells is
            # safe_dist / resolution

            if forbidden_zones is None:
                forbidden_zones = []

            fork = self._world.map.occupancy.fork()

            points: List[Position] = []

            if n < 0:  # TODO profile when this is faster
                for _ in range(n):
                    pos = self._classic_random_pos_on_map(
                        safe_dist=safe_dist, forbidden_zones=forbidden_zones)
                    posr = PositionRadius(*pos, safe_dist)
                    fork.occupy(*self.world.map.tf_posr2rect(posr))
                    forbidden_zones.append(posr)
                    points.append(pos)
                    
                return points

            else:
                max_depth = 10

                for f_zone in forbidden_zones:
                    fork.occupy(
                        *self.world.map.tf_posr2rect(
                            PositionRadius(
                                f_zone.x,
                                f_zone.y,
                                f_zone.radius / self.resolution
                            )
                        )
                    )

                min_dist: float = safe_dist / self.resolution
                available_positions = self._occupancy_to_available(
                    occupancy=fork.grid, safe_dist=min_dist)

                def sample(target: int) -> Collection[Position]:

                    all_banned: np.ndarray = np.zeros((target, 2))
                    banned_index: int = 0

                    result: List[Position] = list()
                    depth: int = 0

                    to_produce = target

                    try:
                        while depth < max_depth:

                            if to_produce > len(available_positions):
                                raise RuntimeError()

                            candidates = available_positions[Config.General.RNG.choice(
                                len(available_positions), to_produce, replace=False), :]

                            for candidate in candidates:

                                banned = all_banned[:banned_index, :]

                                if np.any(np.linalg.norm(banned - candidate, axis=1) < min_dist):
                                    continue

                                all_banned[banned_index] = candidate
                                banned_index += 1

                                fork.occupy(
                                    (candidate-min_dist),
                                    (candidate+min_dist)
                                )
                                
                                result.append(self._world.map.tf_grid2pos(
                                    (candidate[0], candidate[1])))

                            to_produce = target - len(result)
                            if to_produce <= 0:
                                break

                            depth += 1

                        else:
                            raise RuntimeError(
                                f"Failed to find free position after {depth} tries")

                    except RuntimeError:
                        result += [
                            self._world.map.tf_grid2pos(
                                (
                                    (-1-floor(i/5)) * int(self._shape[1]/5),
                                    int((i % 5) * self._shape[0]/5)
                                )
                            ) for i in range(to_produce)]
                        rospy.logerr(f"Couldn't find enough empty cells for {to_produce} requests")
                    
                    finally:
                        return result

                points = list(sample(n))

            if forbid:
                fork.commit()

            # 5. inside get_positions_in_zones: convert zones into masks
            # 6. inside get_positions_in_zones: calculate a point inside the mask and return it
            return points


    def position_on_map(self, safe_dist: float, forbidden_zones: Optional[List[PositionRadius]] = None, forbid: bool = True) -> Position:
        return self.positions_on_map(n=1, safe_dist=safe_dist, forbidden_zones=forbidden_zones)[0]

    # id_gen = itertools.count()

    def _occupancy_to_available(self, occupancy: np.ndarray, safe_dist: float) -> np.ndarray:

        filt_size = int(2 * safe_dist + 1)
        filt = np.full((filt_size, filt_size), 1) / (filt_size ** 2)

        spread = scipy.signal.convolve2d(
            WorldOccupancy.not_full(occupancy).astype(
                np.uint8) * np.iinfo(np.uint8).max,
            filt,
            mode="full",
            boundary="fill",
            fillvalue=int(WorldOccupancy.FULL)
        )

        # import cv2
        # i = next(self.id_gen)
        # cv2.imwrite(f"~/_debug{i}_1.png", occupancy)
        # cv2.imwrite(f"~/_debug{i}_2.png", WorldOccupancy.not_full(occupancy).astype(np.uint8) * np.iinfo(np.uint8).max)
        # cv2.imwrite(f"~/_debug{i}_3.png", spread)
        # cv2.imwrite(f"~/_debug{i}_4.png", WorldOccupancy.empty(spread).astype(np.uint8) * np.iinfo(np.uint8).max)

        return np.transpose(np.where(WorldOccupancy.empty(spread)))
