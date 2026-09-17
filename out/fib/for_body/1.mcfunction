scoreboard players operation c _var_fib = a _var_fib
scoreboard players operation c _var_fib += b _var_fib
tellraw @a [{"score": {"name": "c", "objective": "_var_fib"}}]
scoreboard players operation a _var_fib = b _var_fib
scoreboard players operation b _var_fib = c _var_fib
scoreboard players remove _k0 _var_fib 1
execute if score _k0 _var_fib matches 1.. run function fib/for_body/1
