$data modify $(output) set value []
$execute store result score $(output) _var run data get $(input) 1
