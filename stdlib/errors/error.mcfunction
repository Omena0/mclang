$data modify storage mypack:mem _error_msg set value $(message)
tellraw @a [{"text":"Error: ","color":"red", "bold": true},{"nbt":"_error_msg","storage":"mypack:mem"}]
