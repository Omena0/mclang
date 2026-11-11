data modify storage mypack:mem _ret set value []
data modify storage mypack:mem _ret append from storage mypack:mem _arg0
data modify storage mypack:mem _ret append from storage mypack:mem _arg1
return 0
