data modify storage mypack:mem _ret append from storage mypack:mem _arg0
scoreboard players remove _mul_n _var 1
execute if score _mul_n _var matches 1.. run function stdlib/string/_mul_step with storage mypack:mem