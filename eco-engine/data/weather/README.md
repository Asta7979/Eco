# Weather data

`london_hem.epw` — hourly London weather (EnergyPlus EPW format) from the UK Government's
Home Energy Model repository: https://github.com/communitiesuk/epb-home-energy-model
(`examples/weather_data/London_weather_EnergyPlus_format.epw`).

Licence: MIT, Copyright (c) 2024 Crown Copyright (Ministry of Housing, Communities and
Local Government). Used by `backend/engine/thermal_model.py` and the detailed ISO 52016 run.

Other UK regions are approximated by shifting the hourly dry-bulb temperature by the regional
difference in mean winter temperature (see `REGION_TEMP_OFFSET_C` in thermal_model.py).
