$function type_check with storage mypack:mem {path: "$(input)"}
$execute if score _typeof _var matches 7 run function stdlib/string/find with storage mypack:mem {input: "$(input)", input2: "$(input2)", output: "$(output)", max_matches: "$(max_matches)", reverse: "$(reverse)"}
$execute if score _typeof _var matches 1..6 run function cast_to_string with storage mypack:mem {input: "$(input)", line: $(line)}
$execute if score _typeof _var matches 9 run function type_error with storage mypack:mem {input: "$(input)", expected: "string", line: $(line)}
