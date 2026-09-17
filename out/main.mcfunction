scoreboard players set _arg0 _var 100
function fib
scoreboard players operation x _var_main = _ret _var
tellraw @a [{"score": {"name": "x", "objective": "_var_main"}}]
tellraw @a ["Hello, World!"]
