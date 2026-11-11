data remove storage mypack:mem _typeof_target
$data modify storage mypack:mem _typeof_target set from storage mypack:mem $(path)
summon text_display ~ ~ ~ {Tags:["mypack.typeof"],text:{storage:"mypack:mem",nbt:"_typeof_target"}}
data modify storage mypack:mem _typeof_string set from entity @e[tag=mypack.typeof,limit=1,distance=..1,type=text_display] text
kill @e[tag=mypack.typeof,distance=..1,type=text_display]
data modify storage mypack:mem _typeof_suffix set string storage mypack:mem _typeof_string -2 -1
execute if data storage mypack:mem {_typeof_suffix:"b"} run scoreboard players set _typeof _var 1
execute if data storage mypack:mem {_typeof_suffix:"s"} run scoreboard players set _typeof _var 2
execute unless data storage mypack:mem {_typeof_suffix:"]"} if data storage mypack:mem {_typeof_suffix:""} run scoreboard players set _typeof _var 3
execute if data storage mypack:mem {_typeof_suffix:"l"} run scoreboard players set _typeof _var 4
execute if data storage mypack:mem {_typeof_suffix:"f"} run scoreboard players set _typeof _var 5
execute if data storage mypack:mem {_typeof_suffix:"d"} run scoreboard players set _typeof _var 6
execute if data storage mypack:mem {_typeof_suffix:"\""} run scoreboard players set _typeof _var 7
execute if data storage mypack:mem {_typeof_suffix:"'"} run scoreboard players set _typeof _var 7
execute if data storage mypack:mem {_typeof_suffix:"}"} run scoreboard players set _typeof _var 8
execute if data storage mypack:mem {_typeof_suffix:"]"} run scoreboard players set _typeof _var 9
