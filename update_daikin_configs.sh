dir="configs/devices"
for f in "$dir"/*; do
 # echo "$f"
  tmp="${f##*/}"
  config_name="${tmp%%.*}"
  # echo "$config_name"
  vctl config delete platform.driver devices/bet/$tmp 
  vctl config store platform.driver devices/bet/$tmp $f
done

dir="configs/registry_configs"
vctl config store platform.driver registry_configs/daikin-one.csv configs/registry_configs/daikin-one.csv --csv