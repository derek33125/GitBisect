target datalayout = "e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-f80:128-n8:16:32:64-S128"
target triple = "x86_64-unknown-linux-gnu"

@b = global i32 1
@a = global i16 0
@.str = constant [4 x i8] c"%X\0A\00"

define i32 @main() {
entry:
  %call = call i8 @func_28()
  %0 = load i32, ptr @b, align 4
  %call1 = call i32 (ptr, ...) @printf(ptr @.str, i32 %0)
  ret i32 %call1
}

declare i32 @printf(ptr, ...)

define i8 @func_28() #0 {
entry:
  br label %for.cond

for.cond:                                         ; preds = %for.body, %entry
  %0 = phi i16 [ 0, %entry ], [ %dec, %for.body ]
  %cmp.not = icmp eq i16 %0, -22
  br i1 %cmp.not, label %for.end, label %for.body

for.body:                                         ; preds = %for.cond
  %1 = load i16, ptr @a, align 2
  %cmp29 = icmp sge i16 %1, %0
  %2 = load i32, ptr @b, align 4
  %conv1.i = zext i1 %cmp29 to i32
  %or.i = or i32 %conv1.i, 90
  %and.i = and i32 %2, %or.i
  store i32 %and.i, ptr @b, align 4
  %dec = add i16 %0, -1
  br label %for.cond

for.end:                                          ; preds = %for.cond
  ret i8 0
}

attributes #0 = { "target-features"="+sse4.1" }
