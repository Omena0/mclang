data modify storage mypack:mem _ret set value []
scoreboard players operation _mul_n _var = _arg1 _var
execute if score _mul_n _var matches 1.. run function stdlib/string/_mul_step with storage mypack:mem
return 0