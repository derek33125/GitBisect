define i32 @fallback__wine_dbg_get_channel_flags(i32 %0, i32 %1) {
  %3 = call i32 @strcmp()
  %4 = icmp slt i32 %1, 0
  %5 = select i1 %4, i32 0, i32 %0
  ret i32 %5
}

declare i32 @strcmp()
