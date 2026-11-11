data modify storage mypack:mem _mul_result set value []
data modify storage mypack:mem _mul_result set from storage mypack:mem _mul_power
data modify storage mypack:mem _ret append from storage mypack:mem _mul_result
scoreboard players remove _mul_n _var 1
