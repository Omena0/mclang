$function type_check with storage mypack:mem {path: "$(input)"}
$execute if score _typeof _var matches 7 run function stdlib/string/splice with storage mypack:mem {input: "$(input)", input2: "$(input2)", input3: "$(input3)", output: "$(output)"}
$execute if score _typeof _var matches 1..6 run function cast_to_string with storage mypack:mem {input: "$(input)", line: $(line)}
$execute if score _typeof _var matches 9 run function type_error with storage mypack:mem {input: "$(input)", expected: "string", line: $(line)}
