$function type_check with storage mypack:mem {path: "$(input)"}
$execute if score _typeof _var matches 9 run function type_error with storage mypack:mem {input: "$(input)", expected: "string", line: $(line)}
$execute store result score _cast_temp _var run data get $(input) 1
