# -*- coding: utf-8 -*-

from abc import ABC
from math import acos, cos, radians, sin


class Entity(ABC):
    _default_text = "N/A"

    def __init__(self, latitude: float, longitude: float):
        self.latitude = latitude
        self.longitude = longitude

    def get_distance_from(self, entity: "Entity") -> float:
        lat1, lon1 = radians(self.latitude), radians(self.longitude)
        lat2, lon2 = radians(entity.latitude), radians(entity.longitude)
        return acos(sin(lat1) * sin(lat2) + cos(lat1) * cos(lat2) * cos(lon2 - lon1)) * 6371
