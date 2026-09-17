data modify storage mypack:mem main._s0 set value []
data modify storage mypack:mem main._s1 set value [{"text":"abc "}]
scoreboard players set _t0 _var 32
function main/str_mul_body/1
data modify storage mypack:mem main.a set from storage mypack:mem main._s0
tellraw @a [{"nbt": "main.a", "storage": "mypack:mem"}]
