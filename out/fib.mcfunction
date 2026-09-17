scoreboard players operation n _var_fib = _arg0 _var
scoreboard players set a _var_fib 0
scoreboard players set b _var_fib 1
scoreboard players operation _k0 _var_fib = n _var_fib
function fib/for_body/1
scoreboard players operation _ret _var = a _var_fib
return 0
