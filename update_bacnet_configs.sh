dir="configs/devices"
for f in "$dir"/*; do
 # echo "$f"
  config_name="${f##*/}"
 # echo "$config_name"
  vctl config store platform.driver devices/aql/$config_name $f
done

dir="configs/registry_configs"
for f in "$dir"/*; do
    config_name="${f#*/}"
 # echo "$f"
 # echo "$config_name"
  vctl config store platform.driver $config_name $f --csv
done