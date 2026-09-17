execute unless score _t0 _var matches 1.. run return 0
scoreboard players operation _t1 _var = _t0 _var
scoreboard players operation _t1 _var %= 2
execute if score _t1 _var matches 1.. run function main/str_mul_append/2
scoreboard players operation _t0 _var /= 2
function main/str_mul_double/3
function main/str_mul_body/1
return 0
