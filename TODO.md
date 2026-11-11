
# TODO

## Stdlib

### String ops (names from python)

- Concat (done but wrong):

```mcfunction
$data set storage $(output) set value "$(string1)$(string2)"
```

- Multiply (done):

Use this algorithm:

```py
result = ""
power = original

while n > 0:
    if n & 1:
        result = result + power

    n >>= 1

    if n > 0:
        power = power + power
```

- Length:

Something like this:

```mcfunction
execute store result storage mypack:mem main._len int 1 run data get storage mypack:mem main._s1
```

- Splice:

Something like this:

```mcfunction
$data modify storage mypack:mem main._char set string storage mypack:mem main._s1 $(start) $(end)
```

- Comparison:

If you try to overwrite a storage with something that is identical to the storage it fails.
Then its easy to just store success.

```mcfunction
execute store success storage mypack:mem main._equal byte 1 run data modify storage mypack:mem main._s1 set from storage mypack:mem main._s2
```

- Find:

Sliding window algorithm. Uses Comparison. Returns a list of up to n matches.

- Split:

Use Find to find indexes, then split there with splice.
Return a list.

- Join:

Simply loop the input list and join them.

- Replace:

Use Split to split the string by matches, then use Join with replace result.

Each will have a max matches arg like in python, default 1.

Also reverse variations that match the last ones. Like in python.
