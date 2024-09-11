# Old 1-second configs 
#vctl config store platform.driver submeters_map.csv configs/submeters_map.csv --csv
# vctl config store platform.driver submeters.csv configs/submeters.csv --csv
# vctl config store platform.driver devices/aql/meters/1second configs/submeters.json

# Old main configs
# vctl config store platform.driver devices/aql/meter configs/tk_satec_bfm136.json 
# vctl config store platform.driver tk_satec_bfm136_map.csv configs/tk_satec_bfm136_map.csv --csv
# vctl config store platform.driver tk_satec_bfm136.csv configs/tk_satec_bfm136.csv --csv

vctl config store platform.driver devices/aql/ac_2 configs/AC_2.json ;
vctl config store platform.driver devices/aql/ac_1 configs/AC_1.json ;
vctl config store platform.driver devices/aql/main_service configs/Main_Service.json ;
vctl config store platform.driver aql_meter_map.csv  configs/aql_meter_map.csv --csv ;
vctl config store platform.driver aql_meter.csv  configs/aql_meter.csv --csv ;