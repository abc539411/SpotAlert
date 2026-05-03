# -*- coding: utf-8 -*-

from typing import Any, Dict, Optional
from .entity import Entity


class Airport(Entity):
    def __init__(self, basic_info: Dict = dict(), info: Dict = dict()):
        if basic_info:
            self.__initialize_with_basic_info(basic_info)
        if info:
            self.__initialize_with_info(info)

    def __repr__(self) -> str:
        return "<({}) {} - Altitude: {} - Latitude: {} - Longitude: {}>".format(
            self.icao, self.name, self.altitude, self.latitude, self.longitude
        )

    def __str__(self) -> str:
        return self.__repr__()

    def __get_info(self, info: Any, default: Optional[Any] = None) -> Any:
        default = default if default is not None else self._default_text
        return info if info is not None and info != self._default_text else default

    def __initialize_with_basic_info(self, basic_info: Dict):
        super().__init__(latitude=basic_info["lat"], longitude=basic_info["lon"])
        self.altitude = basic_info["alt"]
        self.name = basic_info["name"]
        self.icao = basic_info["icao"]
        self.iata = basic_info["iata"]
        self.country = basic_info["country"]

    def __initialize_with_info(self, info: Dict):
        super().__init__(
            latitude=info["position"]["latitude"],
            longitude=info["position"]["longitude"],
        )
        self.altitude = info["position"]["altitude"]
        self.name = info["name"]
        self.icao = info["code"]["icao"]
        self.iata = info["code"]["iata"]
        position = info["position"]
        self.country = position["country"]["name"]
        self.country_code = self.__get_info(position.get("country", dict()).get("code"))
        self.city = self.__get_info(position.get("region", dict())).get("city")
        timezone = info.get("timezone", dict())
        self.timezone_name = self.__get_info(timezone.get("name"))
        self.timezone_offset = self.__get_info(timezone.get("offset"))
        self.timezone_offset_hours = self.__get_info(timezone.get("offsetHours"))
        self.timezone_abbr = self.__get_info(timezone.get("abbr"))
        self.timezone_abbr_name = self.__get_info(timezone.get("abbrName"))
        self.visible = self.__get_info(info.get("visible"))
        self.website = self.__get_info(info.get("website"))

    def set_airport_details(self, airport_details: Dict) -> None:
        airport = self.__get_info(airport_details.get("airport"), dict())
        airport = self.__get_info(airport.get("pluginData"), dict())
        details = self.__get_info(airport.get("details"), dict())
        position = self.__get_info(details.get("position"), dict())
        code = self.__get_info(details.get("code"), dict())
        country = self.__get_info(position.get("country"), dict())
        region = self.__get_info(position.get("region"), dict())
        flight_diary = self.__get_info(airport.get("flightdiary"), dict())
        ratings = self.__get_info(flight_diary.get("ratings"), dict())
        schedule = self.__get_info(airport.get("schedule"), dict())
        timezone = self.__get_info(details.get("timezone"), dict())
        aircraft_count = self.__get_info(airport.get("aircraftCount"), dict())
        aircraft_on_ground = self.__get_info(aircraft_count.get("onGround"), dict())
        urls = self.__get_info(details.get("url"), dict())

        self.name = self.__get_info(details.get("name"))
        self.iata = self.__get_info(code.get("iata"))
        self.icao = self.__get_info(code.get("icao"))
        self.altitude = self.__get_info(position.get("elevation"))
        self.latitude = self.__get_info(position.get("latitude"))
        self.longitude = self.__get_info(position.get("longitude"))
        self.country = self.__get_info(country.get("name"))
        self.country_code = self.__get_info(country.get("code"))
        self.country_id = self.__get_info(country.get("id"))
        self.city = self.__get_info(region.get("city"))
        self.timezone_abbr = self.__get_info(timezone.get("abbr"))
        self.timezone_abbr_name = self.__get_info(timezone.get("abbrName"))
        self.timezone_name = self.__get_info(timezone.get("name"))
        self.timezone_offset = self.__get_info(timezone.get("offset"))

        if isinstance(self.timezone_offset, int):
            self.timezone_offset_hours = f"{int(self.timezone_offset / 3600)}:00"
        else:
            self.timezone_offset_hours = self.__get_info(None)

        self.reviews_url = flight_diary.get("url")
        if self.reviews_url and isinstance(self.reviews_url, str):
            self.reviews_url = "https://www.flightradar24.com" + self.reviews_url
        else:
            self.reviews_url = self.__get_info(self.reviews_url)

        self.reviews = self.__get_info(flight_diary.get("reviews"))
        self.evaluation = self.__get_info(flight_diary.get("evaluation"))
        self.average_rating = self.__get_info(ratings.get("avg"))
        self.total_rating = self.__get_info(ratings.get("total"))
        self.weather = self.__get_info(airport.get("weather"), dict())
        self.runways = airport.get("runways", list())
        self.aircraft_on_ground = self.__get_info(aircraft_on_ground.get("total"))
        self.aircraft_visible_on_ground = self.__get_info(aircraft_on_ground.get("visible"))
        self.arrivals = self.__get_info(schedule.get("arrivals"), dict())
        self.departures = self.__get_info(schedule.get("departures"), dict())
        self.website = self.__get_info(urls.get("homepage"))
        self.wikipedia = self.__get_info(urls.get("wikipedia"))
        self.visible = self.__get_info(details.get("visible"))
        self.images = self.__get_info(details.get("airportImages"), dict())
