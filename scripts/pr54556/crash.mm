#include <string>

__attribute__((objc_root_class))
@interface Test
- (void)test:(std::string)x;
@end

@implementation Test
- (void)test:(std::string)x {}
@end

int main() {
  Test *test = 0;
  [test test:""];
}
