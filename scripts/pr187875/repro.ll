; Reduced reproducer from llvm/llvm-project#187875 comment:
; opt -passes=verify repro.ll | lli ; echo $?    => 55
; opt -passes=loop-vectorize repro.ll | lli ; echo $? => 53 on bad revisions

source_filename = "<stdin>"
target datalayout = "e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-f80:128-n8:16:32:64-S128"
target triple = "x86_64-unknown-linux-gnu"

@c = dso_local local_unnamed_addr global i32 0, align 4

define i32 @main(i32 %argc, ptr %argv) local_unnamed_addr {
entry:
  %0 = load i32, ptr @c, align 4
  %tobool.not = icmp eq i32 %0, 0
  br label %for.cond1.preheader

for.cond1.preheader:
  %1 = phi i32 [ 0, %entry ], [ %3, %for.inc6 ]
  %2 = phi i64 [ 0, %entry ], [ %inc, %for.inc6 ]
  br i1 %tobool.not, label %if.end.lr.ph, label %for.inc6

if.end.lr.ph:
  %conv = trunc nuw nsw i64 %2 to i32
  br label %for.inc6

for.inc6:
  %3 = phi i32 [ %1, %for.cond1.preheader ], [ %conv, %if.end.lr.ph ]
  %storemerge.lcssa = phi i32 [ -11, %for.cond1.preheader ], [ -2, %if.end.lr.ph ]
  %inc = add nuw nsw i64 %2, 1
  %exitcond = icmp ne i64 %inc, 56
  br i1 %exitcond, label %for.cond1.preheader, label %for.end7

for.end7:
  ret i32 %3
}
