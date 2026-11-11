$function type_check with storage mypack:mem {path: "$(input)"}
$execute if score _typeof _var matches 7 run function stdlib/string/multiply with storage mypack:mem {input: "$(input)", input2: "$(input2)", output: "$(output)"}
$execute if score _typeof _var matches 1..6 run function cast_to_string with storage mypack:mem {input: "$(input)", line: $(line)}
$execute if score _typeof _var matches 9 run function type_error with storage mypack:mem {input: "$(input)", expected: "string", line: $(line)}
$function type_check with storage mypack:mem {path: "$(input2)"}
$execute if score _typeof _var matches 1..6 run function cast_to_number with storage mypack:mem {input: "$(input2)", line: $(line)}
$execute if score _typeof _var matches 7 run function stdlib/string/multiply with storage mypack:mem {input: "$(input)", input2: "$(input2)", output: "$(output)"}
$execute if score _typeof _var matches 8 run function type_error with storage mypack:mem {input: "$(input2)", expected: "number", line: $(line)}
$execute if score _typeof _var matches 9 run function type_error with storage mypack:mem {input: "$(input2)", expected: "number", line: $(line)}
