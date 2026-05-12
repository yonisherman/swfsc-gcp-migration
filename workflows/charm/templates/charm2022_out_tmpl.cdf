netcdf charm_viirs_forecast_0day_20220424 {
dimensions:
    time = 1 ;
    latitude = 391 ;
    longitude = 351 ;
variables:
    double time(time) ;
        time:_CoordinateAxisType = "Time" ;
        time:axis = "T" ;
        time:calendar = "Gregorian" ;
        time:coverage_content_type = "coordinate" ;
        time:ioos_category = "Time" ;
        time:standard_name = "time" ;
        time:time_origin = "01-JAN-1970 00:00:00" ;
        time:units = "seconds since 1970-01-01T00:00:00Z" ;
        time:comment = "The day represented by the nowcasts" ;
        time:long_name = "Centered Model Run Time" ;
    float latitude(latitude) ;
        latitude:_CoordinateAxisType = "Lat" ;
        latitude:actual_range = 31.3f, 43.f  ;
        latitude:comment = "The latitude values values are the centers of the grid cells" ;
        latitude:coordsys = "geographic" ;
        latitude:coverage_content_type = "coordinate" ;
        latitude:ioos_category = "Time" ;
        latitude:long_name = "Latitude" ;
        latitude:point_spacing = "even" ;
        latitude:standard_name = "latitude" ;
        latitude:units = "degrees_north" ;
        latitude:valid_range = -90.f, 90.f ;
        latitude:axis = "Y" ;
    float longitude(longitude) ;
        longitude:_CoordinateAxisType = "Lon" ;
        longitude:actual_range = 232.5f, 243.f ;
        longitude:coordsys = "geographic" ;
        longitude:comment = "Longitude values are the centers of the grid cells" ;
        longitude:coverage_content_type = "coordinate" ;
        longitude:long_name = "Longitude" ;
        longitude:point_spacing = "even" ;
        longitude:standard_name = "longitude" ;
        longitude:units = "degrees_east" ;
        longitude:axis = "X" ;
    float pseudo_nitzschia(time, latitude, longitude) ;
        pseudo_nitzschia:_FillValue = -99999.f ;
        pseudo_nitzschia:valid_range = 0.f, 1.f ;
        pseudo_nitzschia:coordsys = "geographic" ;
        pseudo_nitzschia:coverage_content_type = "modelResult" ;
        pseudo_nitzschia:ioos_category = "Phytoplankton Species" ;
        pseudo_nitzschia:long_name = "Probability of Pseudo-nitzschia > 10,000 cells/L" ;
        pseudo_nitzschia:missing_value = -99999.f ;
        pseudo_nitzschia:units = "1" ;
    float particulate_domoic(time, latitude, longitude) ;
        particulate_domoic:_FillValue = -99999.f ;
        particulate_domoic:valid_range = 0.f, 1.f ;
        particulate_domoic:coordsys = "geographic" ;
        particulate_domoic:coverage_content_type = "modelResult" ;
        particulate_domoic:ioos_category = "Contaminants" ;
        particulate_domoic:long_name = "Probability of Particulate Domoic Acid > 500 nanograms/L" ;
        particulate_domoic:missing_value = -99999.f ;
        particulate_domoic:units = "1" ;
    float cellular_domoic(time, latitude, longitude) ;
        cellular_domoic:_FillValue = -99999.f ;
        cellular_domoic:valid_range = 0.f, 1.f ;
        cellular_domoic:coordsys = "geographic" ;
        cellular_domoic:coverage_content_type = "modelResult" ;
        cellular_domoic:ioos_category = "Contaminants" ;
        cellular_domoic:long_name = "Probability of Cellular Domoic Acid > 10 picograms/cell" ;
        cellular_domoic:missing_value = -99999.f ;
        cellular_domoic:units = "1" ;
    float chla_filled(time, latitude, longitude) ;
        chla_filled:_FillValue = -99999.f ;
        chla_filled:coordsys = "geographic" ;
        chla_filled:coverage_content_type = "modelResult" ;
        chla_filled:long_name = "NOAA-20 VIIRS Chlorophyll Fields Gap-Filled with DINEOF" ;
        chla_filled:missing_value = -99999.f ;
        chla_filled:standard_name = "mass_concentration_chlorophyll_concentration_in_sea_water" ;
        chla_filled:units = "mg m^-3" ;
    float r489_filled(time, latitude, longitude) ;
        r489_filled:_FillValue = -99999.f ;
        r489_filled:coordsys = "geographic" ;
        r489_filled:coverage_content_type = "modelResult" ;
        r489_filled:long_name = "NOAA-20 VIIRS 489nm Reflectance Fields Gap-Filled with DINEOF" ;
        r489_filled:missing_value = -99999.f ;
    float r556_filled(time, latitude, longitude) ;
        r556_filled:_FillValue = -99999.f ;
        r556_filled:coordsys = "geographic" ;
        r556_filled:coverage_content_type = "modelResult" ;
        r556_filled:long_name = "NOAA-20 VIIRS 556nm Reflectance Fields Gap-Filled with DINEOF" ;
        r556_filled:missing_value = -99999.f ;
    float salinity(time, latitude, longitude) ;
        salinity:_FillValue = -99999.f ;
        salinity:coordsys = "geographic" ;
        salinity:coverage_content_type = "modelResult" ;
        salinity:long_name = "WCOFS Surface Salinity" ;
        salinity:missing_value = -99999.f ;
    float water_temparture(time, latitude, longitude) ;
        water_temparture:_FillValue = -99999.f ;
        water_temparture:coordsys = "geographic" ;
        water_temparture:coverage_content_type = "modelResult" ;
        water_temparture:long_name = "WCOFS Surface Water Temperature" ;
        water_temparture:missing_value = -99999.f ;

// global attributes:
        :cdm_data_type = "Grid" ;
        :comment = "" ;
        :composite = "true" ;
        :contributor_email = "dale.robinson@noaa.gov, cedwards@ucsc.edu, kudela@ucsc.edu, cra002@ucsd.edu" ;
        :contributor_name = "Dale Robinson, Christopher Edwards, Raphe Kudela, Clarissa Anderson, NOAA NESDIS" ;
        :contributor_role = "Operationalizing, Lead PI, Co-PI, Co-PI, Source of level 2 data" ;
        :contributor_url = "https://coastwatch.pfeg.noaa.gov, https://oceanmodeling.ucsc.edu, http://oceandatacenter.ucsc.edu/home/ https://www.sccoos.org" ;
        :Conventions = "CF-1.6, COARDS, ACDD-1.3" ;
        :creator_name = "Clarissa Anderson" ;
        :creator_type = "person" ;
        :creator_email = "cra002@ucsd.edu" ;
        :creator_url = "https://www.cencoos.org/observations/models-forecasts" ;
        :geospatial_lat_max = 43.f ;
        :geospatial_lat_min = 31.3f ;
        :geospatial_lat_resolution = 0.03f ;
        :geospatial_lat_units = "degrees_north" ;
        :geospatial_lon_max = 243.f ;
        :geospatial_lon_min = 232.5f ;
        :geospatial_lon_resolution = 0.03f ;
        :geospatial_lon_units = "degrees_east" ;
        :institution = "NOAA/NMFS/SWFSC/ERD, CoastWatch West Coast" ;
        :license = "The data may be used and redistributed for free but is not intended for legal use, since it may contain inaccuracies. Neither the data Contributor, CoastWatch, NOAA, nor the United States Government, nor any of their employees or contractors, makes any warranty, express or implied, including warranties of merchantability and fitness for a particular purpose, or assumes any legal liability for the accuracy, completeness, or usefulness, of this information." ;
        :naming_authority = "gov.noaa.pfeg.coastwatch" ;
        :processing_level = "L4 Mapped" ;
        :product_name = "C-HARM v3.1 model output with SNPP VIIRS and WCOFS data" ;
        :product_version = "3.1" ;
        :platform = "VIIRS" ;
        :project = "Advancing the West Coast Ocean Forecasting System through Assessment, Model Development, and Ecological Products" ;
        :projection = "geographic" ;
        :publisher_email = "erd.data@noaa.gov" ;
        :publisher_name = "NOAA NMFS SWFSC ERD" ;
        :publisher_type = "institution" ;
        :publisher_url = "https://coastwatch.pfeg.noaa.gov" ;
        :satellite = "NOAA-20" ;
        :source = "Satellite data, C-HARM model output, and WCOFS model output" ;
        :spatial_resolution = "3 km" ;
        :summary = "The C-HARM model generates nowcast and forecasts of the probability of Pseudo-nitzschia concentrations of in excess of 10,000 cells/L, the probability of particulate domoic acid > 500 nanograms/L, and the probability of cellular domoic acid > 10 picograms/cell in California and Southern Oregon coastal water. Inputs for the model include near real-time satellite NOAA NOAA-20 VIIRS observations gap-filled chlorophyll a, 489nm reflectance, and 556nm reflectance fields from the NOAA-20 VIIRS sensor plus nowcast and forecast data of surface salinity, sea surface temperature, and surface currents from WCOFS ROMS. The chlorophyll a, reflectance, temperature, and surface current fields are included in the dataset." ;
        :time_coverage_resolution = "PD1" ;
        :keywords = "CoastWatch West Coast, WCOFS, VIIRS, NOAA-20, 488nm, 555nm, acid, air, atmosphere, atmospheric, biological, biosphere, c-harm, california, cell, cells, cells/l, cellular, cellular_domoic, cencoos, chemistry, chla, chla_filled, chlorophyll, chlorophyll-a, classification, coast, coastal, coastwatch, color, concentration, concentration_of_chlorophyll_in_sea_water, data, deigo, diatoms, dineof, domoic, domoic acid, downwelling, earth, Earth Science > Atmosphere > Atmospheric Radiation > Radiative Flux, Earth Science > Biological Classification > Protists > Diatoms, Earth Science > Biological Classification > Protists > Plankton > Phytoplankton, Earth Science > Biosphere > Ecosystems > Marine Ecosystems > Coastal, Earth Science > Ocean > Ocean Temperature > Sea Surface Temperature, Earth Science > Oceans > Coastal Processes, Earth Science > Oceans > Ocean Chemistry > Chlorophyll, Earth Science > Oceans > Ocean Optics > Radiance, eastern, ecosystems, emerging, fields, filled, flux, forecast, habs, harm, harmful algal blooms, heat, heat flux, imaging, latitude, longitude, marine, model, modeling, moderate, modis, nanograms, nanograms/l, near, nitzschia, noaa, nowcast, nrt, observing, ocean, OCEAN > PACIFIC OCEAN > EASTERN PACIFIC OCEAN > CALIFORNIA, ocean color, oceans, optics, oregon, pacific, particular, particulate, particulate_domoic, phytoplankton, picograms, picograms/cell, plankton, probability, processes, protists, pseudo, pseudo-nitzschia, pseudo_nitzschia, Rrs489, Rrs556, radiance, radiation, radiative, ratio, real, reflectance, regional, resolution, roms,  sccoos, science, sea, seawater, sst, surface, surface_ratio_of_upwelling_radiance_emerging_from_sea_water_to_downwelling_radiative_flux_in_air, temperature, time, ucsc, ucsd, university, water" ;
        :history = "DINEOF gap filling was applied to daily NOAA-20 VIIRS chlorophyll, 556nm reflectance, and 489nm reflectance fields extending back 180 days from model run date.\n The gap-filled data plus salinity and temperature data from the WCOFS model were used as inputs to the C-HARM model to obtain nowcast. \n ROMS current forecasts were used to advect the NOAA VIIRS data 1, 2, and 3 days into the future. Data gaps resulting from the advection were filled with a second DINEOF. \n The advected gap-filled data plus salinity and temperature WCOFS forecast data were used as inputs to the C-HARM model to obtain forecasts for 1, 2, and 3 days into the future." ;
        :title = "C-HARM v3.1 Nowcast, Pseudo-nitzschia, cellular domoic acid, and particular domoic acid probability, California and Southern Oregon coast, 2022-present" ;
        :id = "charmForecast0dayV3.1" ;
data:

 time = 1650801600 ;

 latitude = 31.3, 31.33, 31.36, 31.39, 31.42, 31.45, 31.48, 31.51, 31.54, 
    31.57, 31.6, 31.63, 31.66, 31.69, 31.72, 31.75, 31.78, 31.81, 31.84, 
    31.87, 31.9, 31.93, 31.96, 31.99, 32.02, 32.05, 32.08, 32.11, 32.14, 
    32.17, 32.2, 32.23, 32.26, 32.29, 32.32, 32.35, 32.38, 32.41, 32.44, 
    32.47, 32.5, 32.53, 32.56, 32.59, 32.62, 32.65, 32.68, 32.71, 32.74, 
    32.77, 32.8, 32.83, 32.86, 32.89, 32.92, 32.95, 32.98, 33.01, 33.04, 
    33.07, 33.1, 33.13, 33.16, 33.19, 33.22, 33.25, 33.28, 33.31, 33.34, 
    33.37, 33.4, 33.43, 33.46, 33.49, 33.52, 33.55, 33.58, 33.61, 33.64, 
    33.67, 33.7, 33.73, 33.76, 33.79, 33.82, 33.85, 33.88, 33.91, 33.94, 
    33.97, 34, 34.03, 34.06, 34.09, 34.12, 34.15, 34.18, 34.21, 34.24, 34.27, 
    34.3, 34.33, 34.36, 34.39, 34.42, 34.45, 34.48, 34.51, 34.54, 34.57, 
    34.6, 34.63, 34.66, 34.69, 34.72, 34.75, 34.78, 34.81, 34.84, 34.87, 
    34.9, 34.93, 34.96, 34.99, 35.02, 35.05, 35.08, 35.11, 35.14, 35.17, 
    35.2, 35.23, 35.26, 35.29, 35.32, 35.35, 35.38, 35.41, 35.44, 35.47, 
    35.5, 35.53, 35.56, 35.59, 35.62, 35.65, 35.68, 35.71, 35.74, 35.77, 
    35.8, 35.83, 35.86, 35.89, 35.92, 35.95, 35.98, 36.01, 36.04, 36.07, 
    36.1, 36.13, 36.16, 36.19, 36.22, 36.25, 36.28, 36.31, 36.34, 36.37, 
    36.4, 36.43, 36.46, 36.49, 36.52, 36.55, 36.58, 36.61, 36.64, 36.67, 
    36.7, 36.73, 36.76, 36.79, 36.82, 36.85, 36.88, 36.91, 36.94, 36.97, 37, 
    37.03, 37.06, 37.09, 37.12, 37.15, 37.18, 37.21, 37.24, 37.27, 37.3, 
    37.33, 37.36, 37.39, 37.42, 37.45, 37.48, 37.51, 37.54, 37.57, 37.6, 
    37.63, 37.66, 37.69, 37.72, 37.75, 37.78, 37.81, 37.84, 37.87, 37.9, 
    37.93, 37.96, 37.99, 38.02, 38.05, 38.08, 38.11, 38.14, 38.17, 38.2, 
    38.23, 38.26, 38.29, 38.32, 38.35, 38.38, 38.41, 38.44, 38.47, 38.5, 
    38.53, 38.56, 38.59, 38.62, 38.65, 38.68, 38.71, 38.74, 38.77, 38.8, 
    38.83, 38.86, 38.89, 38.92, 38.95, 38.98, 39.01, 39.04, 39.07, 39.1, 
    39.13, 39.16, 39.19, 39.22, 39.25, 39.28, 39.31, 39.34, 39.37, 39.4, 
    39.43, 39.46, 39.49, 39.52, 39.55, 39.58, 39.61, 39.64, 39.67, 39.7, 
    39.73, 39.76, 39.79, 39.82, 39.85, 39.88, 39.91, 39.94, 39.97, 40, 40.03, 
    40.06, 40.09, 40.12, 40.15, 40.18, 40.21, 40.24, 40.27, 40.3, 40.33, 
    40.36, 40.39, 40.42, 40.45, 40.48, 40.51, 40.54, 40.57, 40.6, 40.63, 
    40.66, 40.69, 40.72, 40.75, 40.78, 40.81, 40.84, 40.87, 40.9, 40.93, 
    40.96, 40.99, 41.02, 41.05, 41.08, 41.11, 41.14, 41.17, 41.2, 41.23, 
    41.26, 41.29, 41.32, 41.35, 41.38, 41.41, 41.44, 41.47, 41.5, 41.53, 
    41.56, 41.59, 41.62, 41.65, 41.68, 41.71, 41.74, 41.77, 41.8, 41.83, 
    41.86, 41.89, 41.92, 41.95, 41.98, 42.01, 42.04, 42.07, 42.1, 42.13, 
    42.16, 42.19, 42.22, 42.25, 42.28, 42.31, 42.34, 42.37, 42.4, 42.43, 
    42.46, 42.49, 42.52, 42.55, 42.58, 42.61, 42.64, 42.67, 42.7, 42.73, 
    42.76, 42.79, 42.82, 42.85, 42.88, 42.91, 42.94, 42.97, 43 ;

 longitude = 232.5, 232.53, 232.56, 232.59, 232.62, 232.65, 232.68, 232.71, 
    232.74, 232.77, 232.8, 232.83, 232.86, 232.89, 232.92, 232.95, 232.98, 
    233.01, 233.04, 233.07, 233.1, 233.13, 233.16, 233.19, 233.22, 233.25, 
    233.28, 233.31, 233.34, 233.37, 233.4, 233.43, 233.46, 233.49, 233.52, 
    233.55, 233.58, 233.61, 233.64, 233.67, 233.7, 233.73, 233.76, 233.79, 
    233.82, 233.85, 233.88, 233.91, 233.94, 233.97, 234, 234.03, 234.06, 
    234.09, 234.12, 234.15, 234.18, 234.21, 234.24, 234.27, 234.3, 234.33, 
    234.36, 234.39, 234.42, 234.45, 234.48, 234.51, 234.54, 234.57, 234.6, 
    234.63, 234.66, 234.69, 234.72, 234.75, 234.78, 234.81, 234.84, 234.87, 
    234.9, 234.93, 234.96, 234.99, 235.02, 235.05, 235.08, 235.11, 235.14, 
    235.17, 235.2, 235.23, 235.26, 235.29, 235.32, 235.35, 235.38, 235.41, 
    235.44, 235.47, 235.5, 235.53, 235.56, 235.59, 235.62, 235.65, 235.68, 
    235.71, 235.74, 235.77, 235.8, 235.83, 235.86, 235.89, 235.92, 235.95, 
    235.98, 236.01, 236.04, 236.07, 236.1, 236.13, 236.16, 236.19, 236.22, 
    236.25, 236.28, 236.31, 236.34, 236.37, 236.4, 236.43, 236.46, 236.49, 
    236.52, 236.55, 236.58, 236.61, 236.64, 236.67, 236.7, 236.73, 236.76, 
    236.79, 236.82, 236.85, 236.88, 236.91, 236.94, 236.97, 237, 237.03, 
    237.06, 237.09, 237.12, 237.15, 237.18, 237.21, 237.24, 237.27, 237.3, 
    237.33, 237.36, 237.39, 237.42, 237.45, 237.48, 237.51, 237.54, 237.57, 
    237.6, 237.63, 237.66, 237.69, 237.72, 237.75, 237.78, 237.81, 237.84, 
    237.87, 237.9, 237.93, 237.96, 237.99, 238.02, 238.05, 238.08, 238.11, 
    238.14, 238.17, 238.2, 238.23, 238.26, 238.29, 238.32, 238.35, 238.38, 
    238.41, 238.44, 238.47, 238.5, 238.53, 238.56, 238.59, 238.62, 238.65, 
    238.68, 238.71, 238.74, 238.77, 238.8, 238.83, 238.86, 238.89, 238.92, 
    238.95, 238.98, 239.01, 239.04, 239.07, 239.1, 239.13, 239.16, 239.19, 
    239.22, 239.25, 239.28, 239.31, 239.34, 239.37, 239.4, 239.43, 239.46, 
    239.49, 239.52, 239.55, 239.58, 239.61, 239.64, 239.67, 239.7, 239.73, 
    239.76, 239.79, 239.82, 239.85, 239.88, 239.91, 239.94, 239.97, 240, 
    240.03, 240.06, 240.09, 240.12, 240.15, 240.18, 240.21, 240.24, 240.27, 
    240.3, 240.33, 240.36, 240.39, 240.42, 240.45, 240.48, 240.51, 240.54, 
    240.57, 240.6, 240.63, 240.66, 240.69, 240.72, 240.75, 240.78, 240.81, 
    240.84, 240.87, 240.9, 240.93, 240.96, 240.99, 241.02, 241.05, 241.08, 
    241.11, 241.14, 241.17, 241.2, 241.23, 241.26, 241.29, 241.32, 241.35, 
    241.38, 241.41, 241.44, 241.47, 241.5, 241.53, 241.56, 241.59, 241.62, 
    241.65, 241.68, 241.71, 241.74, 241.77, 241.8, 241.83, 241.86, 241.89, 
    241.92, 241.95, 241.98, 242.01, 242.04, 242.07, 242.1, 242.13, 242.16, 
    242.19, 242.22, 242.25, 242.28, 242.31, 242.34, 242.37, 242.4, 242.43, 
    242.46, 242.49, 242.52, 242.55, 242.58, 242.61, 242.64, 242.67, 242.7, 
    242.73, 242.76, 242.79, 242.82, 242.85, 242.88, 242.91, 242.94, 242.97, 
    243 ;
}
