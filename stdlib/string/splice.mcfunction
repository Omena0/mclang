$data modify $(output) set from storage mypack:mem $(input)
$execute store result score $(output) _var run data get $(input) 1
