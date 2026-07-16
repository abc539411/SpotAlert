# -*- coding: utf-8 -*-

from typing import Any, Dict, List, Optional
from .entity import Entity


class Flight(Entity):
    def __init__(self, flight_id: str, info: List[Any]):
        super().__init__(latitude=self.__get_info(info[1]), longitude=self.__get_info(info[2]))
        self.id = flight_id
        self.icao_24bit = self.__get_info(info[0])
        self.heading = self.__get_info(info[3])
        self.altitude = self.__get_info(info[4])
        self.ground_speed = self.__get_info(info[5])
        self.squawk = self.__get_info(info[6])
        self.aircraft_code = self.__get_info(info[8])
        self.registration = self.__get_info(info[9])
        self.time = self.__get_info(info[10])
        self.origin_airport_iata = self.__get_info(info[11])
        self.destination_airport_iata = self.__get_info(info[12])
        self.number = self.__get_info(info[13])
        self.airline_iata = self.__get_info(info[13][:2])
        self.on_ground = self.__get_info(info[14])
        self.vertical_speed = self.__get_info(info[15])
        self.callsign = self.__get_info(info[16])
        self.airline_icao = self.__get_info(info[18])

    def __repr__(self) -> str:
        return self.__str__()

    def __str__(self) -> str:
        return "<({}) {} - Altitude: {} - Ground Speed: {} - Heading: {}>".format(
            self.aircraft_code, self.registration, self.altitude, self.ground_speed, self.heading
        )

    def __get_info(self, info: Any, default: Optional[Any] = None) -> Any:
        default = default if default is not None else self._default_text
        return info if info is not None and info != self._default_text else default

    def check_info(self, **info: Any) -> bool:
        comparison_functions = {"max": max, "min": min}
        for key, value in info.items():
            prefix, key = key.split("_", maxsplit=1) if key[:4] in ("max_", "min_") else (None, key)
            if prefix and key in self.__dict__:
                if comparison_functions[prefix](value, self.__dict__[key]) != value:
                    return False
            elif key in self.__dict__ and value != self.__dict__[key]:
                return False
        return True

    def set_flight_details(self, flight_details: Dict) -> None:
        aircraft = self.__get_info(flight_details.get("aircraft"), dict())
        airline = self.__get_info(flight_details.get("airline"), dict())
        airport = self.__get_info(flight_details.get("airport"), dict())

        dest_airport = self.__get_info(airport.get("destination"), dict())
        dest_airport_code = self.__get_info(dest_airport.get("code"), dict())
        dest_airport_info = self.__get_info(dest_airport.get("info"), dict())
        dest_airport_position = self.__get_info(dest_airport.get("position"), dict())
        dest_airport_country = self.__get_info(dest_airport_position.get("country"), dict())
        dest_airport_timezone = self.__get_info(dest_airport.get("timezone"), dict())

        orig_airport = self.__get_info(airport.get("origin"), dict())
        orig_airport_code = self.__get_info(orig_airport.get("code"), dict())
        orig_airport_info = self.__get_info(orig_airport.get("info"), dict())
        orig_airport_position = self.__get_info(orig_airport.get("position"), dict())
        orig_airport_country = self.__get_info(orig_airport_position.get("country"), dict())
        orig_airport_timezone = self.__get_info(orig_airport.get("timezone"), dict())

        history = self.__get_info(flight_details.get("flightHistory"), dict())
        status = self.__get_info(flight_details.get("status"), dict())

        self.aircraft_age = self.__get_info(aircraft.get("age"))
        self.aircraft_country_id = self.__get_info(aircraft.get("countryId"))
        self.aircraft_history = history.get("aircraft", list())
        self.aircraft_images = aircraft.get("images", list())
        self.aircraft_model = self.__get_info(self.__get_info(aircraft.get("model"), dict()).get("text"))

        self.airline_name = self.__get_info(airline.get("name"))
        self.airline_short_name = self.__get_info(airline.get("short"))

        self.destination_airport_altitude = self.__get_info(dest_airport_position.get("altitude"))
        self.destination_airport_country_code = self.__get_info(dest_airport_country.get("code"))
        self.destination_airport_country_name = self.__get_info(dest_airport_country.get("name"))
        self.destination_airport_latitude = self.__get_info(dest_airport_position.get("latitude"))
        self.destination_airport_longitude = self.__get_info(dest_airport_position.get("longitude"))
        self.destination_airport_icao = self.__get_info(dest_airport_code.get("icao"))
        self.destination_airport_baggage = self.__get_info(dest_airport_info.get("baggage"))
        self.destination_airport_gate = self.__get_info(dest_airport_info.get("gate"))
        self.destination_airport_name = self.__get_info(dest_airport.get("name"))
        self.destination_airport_terminal = self.__get_info(dest_airport_info.get("terminal"))
        self.destination_airport_visible = self.__get_info(dest_airport.get("visible"))
        self.destination_airport_website = self.__get_info(dest_airport.get("website"))
        self.destination_airport_timezone_abbr = self.__get_info(dest_airport_timezone.get("abbr"))
        self.destination_airport_timezone_name = self.__get_info(dest_airport_timezone.get("name"))
        self.destination_airport_timezone_offset = self.__get_info(dest_airport_timezone.get("offset"))
        self.destination_airport_timezone_offset_hours = self.__get_info(dest_airport_timezone.get("offsetHours"))

        self.origin_airport_altitude = self.__get_info(orig_airport_position.get("altitude"))
        self.origin_airport_country_code = self.__get_info(orig_airport_country.get("code"))
        self.origin_airport_country_name = self.__get_info(orig_airport_country.get("name"))
        self.origin_airport_latitude = self.__get_info(orig_airport_position.get("latitude"))
        self.origin_airport_longitude = self.__get_info(orig_airport_position.get("longitude"))
        self.origin_airport_icao = self.__get_info(orig_airport_code.get("icao"))
        self.origin_airport_baggage = self.__get_info(orig_airport_info.get("baggage"))
        self.origin_airport_gate = self.__get_info(orig_airport_info.get("gate"))
        self.origin_airport_name = self.__get_info(orig_airport.get("name"))
        self.origin_airport_terminal = self.__get_info(orig_airport_info.get("terminal"))
        self.origin_airport_visible = self.__get_info(orig_airport.get("visible"))
        self.origin_airport_website = self.__get_info(orig_airport.get("website"))
        self.origin_airport_timezone_abbr = self.__get_info(orig_airport_timezone.get("abbr"))
        self.origin_airport_timezone_name = self.__get_info(orig_airport_timezone.get("name"))
        self.origin_airport_timezone_offset = self.__get_info(orig_airport_timezone.get("offset"))
        self.origin_airport_timezone_offset_hours = self.__get_info(orig_airport_timezone.get("offsetHours"))

        self.status_icon = self.__get_info(status.get("icon"))
        self.status_text = self.__get_info(status.get("text"))
        self.time_details = self.__get_info(flight_details.get("time"), dict())
        self.trail = flight_details.get("trail", list())
