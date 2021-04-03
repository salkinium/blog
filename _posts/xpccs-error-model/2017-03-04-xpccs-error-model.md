---
layout: post
title: "The Curious Case of xpcc's Error Model"
---

In hindsight it is quite apparent that [xpcc](http://xpcc.io) and therefore also the [@RCA_eV robot code](https://twitter.com/RCA_eV) was missing a good error model.
Until now xpcc's way of dealing with failures included using `static_assert` at compile time and returning error codes at runtime whenever it was deemed necessary. We never considered runtime assertions, nor catching hardware errors like the ARM Cortex-M Fault exceptions. We crashed and burned, a few times literally.

So what can we do that is simple to use and efficient on AVR and Cortex-M devices, but still powerful enough to be useful? It's time we thought about our error model.

<!--more-->

## The Problem

[The RCA robots](http://www.roboterclub.rwth-aachen.de/) are controlled by a number of software components that communicate by Remote Procedure Calls (PRCs) via an event loop locally or over CAN.
We call this Cross Platform Component Communication (XPCC) and it's an under-appreciated (and under-documented) [part of the xpcc framework](http://xpcc.io/api/group__xpcc__comm.html).
It allows us to distribute components over many microcontrollers if needed and helps us understand what is happening in the robot at runtime by listening in on the CAN bus.

However, we are constantly fine tuning our robots before and after a match and if we accidentally leave the CAN bus disconnected the robot turns into a (very expensive) paper weight and we loose the game. It is therefore paramount that we detect this situation on CAN initialization and let the robot emit loud and annoying sounds so that the ~~slaves~~ students can fix it. There are several other places in the initialization that must not fail for the same reason.

It wasn't clear to us how and where to handle this type of failure though. Should the initialization code return an error code? What if we forgot to check it? Isn't this a recurring problem?
It seemed like a good opportunity to heartily consult The Internet™ on the topic of error models, since surely other, smarter people have solved this problem already. Oh boy.

## The Research

[Joe Duffy wrote a fantastically detailed article on the many considerations that went into the error model used in the Midori research project](http://joeduffyblog.com/2016/02/07/the-error-model/). (You should read his [entire series on Midori](http://joeduffyblog.com/2015/11/03/blogging-about-midori/), there is a lot of gold there.)

There are couple of points in there that resonated very strongly with me:

1. ["Unchecked Exceptions"](http://joeduffyblog.com/2016/02/07/the-error-model/#unchecked-exceptions): We can't use C++ exceptions since the AVR toolchain does not support it. But even if we could, we wouldn't, for the many reasons pointed out in this section. It's actually quite horrifying to me how bad a match C++ exception are for a reliable system.
1. ["To Build a Reliable System"](http://joeduffyblog.com/2016/02/07/the-error-model/#to-build-a-reliable-system): XPCC deals with failures prominently: RPC delivery can fail, components can decline RPCs ("I'm busy") or simply fail during their execution ("I couldn't grab this object"). We have to deal with these failures in order to get a reliable system that doesn't get stuck on the first failure. You'd be surprised how many failures there can be during a Eurobot game under real world conditions. The fact that we can relatively simply retry actions or ultimately give up and move on is actually quite amazing.
1. ["Bugs Aren’t Recoverable Errors!"](http://joeduffyblog.com/2016/02/07/the-error-model/#bugs-arent-recoverable-errors): This was the most important realization for me. When we are talking about the system clock or the CAN bus not initializing correctly, these are bugs. You cannot recover from them and the robot is stuck. However, XPCC failures as described above are recoverable errors and it's fine for them to happen happen in normal operation.
1. ["Abandonment"](http://joeduffyblog.com/2016/02/07/the-error-model/#abandonment): xpcc didn't have a concept of abandonment and it doesn't call any libc `exit()` functions. There are a couple of `while(1)` loops in the vector table (and hard fault handler), but there is no controlled teardown (with reporting) of failures. It's crash'n'burn all the way down.

Of course Midori's goal of writing an entire operating system from scratch is a little higher on the scale of epicness than us coding our robots. And considering that they rolled their own language and compiler to implement this error model, it's pretty clear that our solution can't really compete with their very thorough approach.

## The Proposal

We propose to continue returning error codes for recoverable errors but use assertions for bugs which can lead to abandonment. There is something appealing about the simplicity of using an `assert(condition)` in the code, so we decided to expand the function signature:
```c
xpcc_assert(bool condition, char *module, char *location, char *failure, uintptr_t context = 0);
```

Yes, we're using C-style `"strings"` to declare the assertion location and failure type instead of using enumerations or similar.
We came to the conclusion that it is a lot simpler to encode structured information using strings rather than keeping all error enumerations in sync to prevent duplicates.
Strings also consume significantly less memory than using a stringified test condition or a "pretty" function string, or even just `__LINE__` and `__FILE__` strings. It also makes it trivial to print the failure.
It made sense to us that the developer writing code with assertions categorizes the failure for the developer calling the code. It's often difficult to assess the exact reason *why* an assertion failed from the stringified test condition alone.

When an assertion fails, it calls all registered assertion handlers one by one.
Assertion handlers have this signature:
```c
Abondonment handler(char *module, char *location, char *failure, uintptr_t context);
```

The identifiers allows these failure handlers to assess the scope and type of failure programmatically and return `Fail`, `DontCare` or `Ignore`.
If any of them returns `Fail` or all of them return `DontCare`, then execution is abandoned. Otherwise if at least one of them `Ignore` the assertion, execution continues.
This allows us to ignore some select failures that we don't care about.

The abandonment handler is called last and has the same signature as the assertion handler. It is required that all assertion handlers are not blocking, so that they can all get called, and whatever blocking code is required can then run in the abandonment handler, where execution is trapped until the next reset anyway.

## The Example

For our problem with CAN bus timout, an assertion is called and the `context` contains the instance of the CAN (`1` or `2`) that failed initialization.

```cpp
void Can1::initialize()
{
    // [...] initialize CAN peripheral
    // wait for CAN bus to be ready
    int deadlockPreventer = 1000; // max ~1ms
    while (not busIsReady() and (deadlockPreventer-- > 0))
        xpcc::delayMicroseconds(1);

    xpcc_assert(deadlockPreventer > 0, "can", "init", "timeout", 1);
}
```

An assertion handler then compares the first three characters to `"can"` and return `Fail` and execution is abandoned:

```cpp
xpcc::Abandonment can_assertion_handler(char *module, char *, char *, uintptr_t)
{
    if (!strncmp(module, "can", 3)) {
        return xpcc::Abandonment::Fail;
    }
    return xpcc::Abandonment::DontCare;
}
// Register assertion handler with system
XPCC_ASSERTION_HANDLER(can_assertion_handler);
```

The abandon handler finally prints the failed assertion to the log and makes some loud bleepy noises:

```c
void xpcc_abandon(char *module, char *location, char *failure, uintptr_t context)
{
    XPCC_LOG_ERROR.printf("Assertion '%s.%s.%s' (0x%p) failed! Abandoning!\n",
                          module, location, failure, context);
    // Make some noise!
    PiezoBuzzer::setOutput();
    while(1) {
        PiezoBuzzer::set();
        xpcc::delayMilliseconds(200);
        PiezoBuzzer::reset();
        xpcc::delayMilliseconds(100);
    }
}
```

On an STM32 this prints:
```
Assertion 'can.init.timeout' (0x00000001) failed! Abandoning!
```

We also log internal robot state via UART backed by a ring buffer of fixed size. If too much is logged at once, the buffer runs out of space, and we loose log output, which is undesirable. However, we cannot wait synchronously for space to become available in the buffer either, as this would impair the timing loops in our robot code.
Since continuing the game is obviously more important than preserving the log, we therefore ignore this failure in game mode:

```cpp
Abandonment logger_buffer_overflow(char *module, char *location, char *failure, uintptr_t)
{
    if (!strncmp(module, "uart", 4) and
        !strncmp(location, "tx", 2) and
        !strncmp(failure, "overflow", 8)) {
        return xpcc::Abandonment::Ignore;
    }
    return xpcc::Abandonment::DontCare;
}
// Register assertion handler with system
XPCC_ASSERTION_HANDLER(logger_buffer_overflow);
```

Note how the assertion handlers only react to the failures they care about and otherwise leaving the decision to other, potentially more specialized handlers.

## The Implementation

Since we want to use assertions a lot in our code, but still keep the code size overhead as low as possible, we use two optimizations: `xpcc_assert` is actually a macro which:
1. moves the condition test out of the function into the calling context, and
2. concatenates the module, location and failure strings into one big string.

```
#define xpcc_assert(condition, module, location, failure, context) \
    if (condition) {} else { \
        xpcc_assert_fail(FLASH_STORAGE(module "\0" location "\0" failure), (uintptr_t) context); }
```

We cannot change that the test condition has to always be evaluated, but we don't have to pass it as an argument into the assert function. That would require the compiler to cast the test result into a numeric value and move it into a register to comply with the ABI. If we branch outside of the assertion, the compiler can test the CPU flags directly.

Similarly, by concatenating the assertion identifier strings into one long string, the compiler only has to populate one register so it can save the code that fetches the other two pointers. (ARMv7-M use literal pools for constants, while AVRs generate them ad-hoc using several load instructions, both actually quite expensive for code size.) The `xpcc_assert_fail` function then breaks the long string apart and passes them to the failure handlers as individual arguments.

Also note the `FLASH_STORAGE` macro, which keeps the strings in Flash on AVRs and thus does not use any SRAM as it would normally do. This means that assertion handlers on AVRs need to use the `*_P` variants of the string compare functions. This is an acceptable caveat for us, since assertion and abandon handlers are part of the application and not the library and there don't need to be shared across platforms.

### Registering assertion handlers

The tricky part is how to register the assertion handlers to the `xpcc_assert_fail` function. We use the linker to collect all assertion handlers across the entire executable and place pointers to them into the same linker section using the `XPCC_ASSERTION_HANDLER` macro. Note how it forces the assertion handler to have the right signature by using the `xpcc::AssertionHandler` type:
```
#define XPCC_ASSERTION_HANDLER(handler) \
    __attribute__((section(XPCC_ASSERTION_LINKER_SECTION), used)) \
    const xpcc::AssertionHandler \
    handler ## _assertion_handler_ptr = handler
```

Adding custom linker sections to ARM Cortex-M devices is trivial, especially since xpcc generates the linkerscript from a central template. It's literally just adding these lines:
```ld
.assertion : ALIGN(4)
{
    __assertion_table_start = .;
    KEEP(*(.assertion))
    __assertion_table_end = .;
} >FLASH
```
The code for `xpcc_assert_fail` which calls all assertion handlers is pretty simple. `xpcc_abandon` here is a weak function that can be overwritten by the application:
```c++
extern AssertionHandler __assertion_table_start;
extern AssertionHandler __assertion_table_end;

void xpcc_assert_fail(const char * identifier, uintptr_t context)
{
    // split up the identifier back into three pointers
    const char * module = identifier;
    const char * location = module + strlen(module) + 1;
    const char * failure = location + strlen(location) + 1;

    // initialize with DontCare in case no assertion handlers were registered
    Abandonment state = Abandonment::DontCare;
    // call all assertion handlers
    AssertionHandler * handler = &__assertion_table_start;
    for (; handler < &__assertion_table_end; handler++)
    {
        state |= (*handler)(module, location, failure, context);
    }
    // abandon if all returned DontCare, or any returned
    if (state == Abandonment::DontCare or
        state & Abandonment::Fail)
    {
        xpcc_abandon(module, location, failure, context);
        while(1) ;
    }
}
```

This code is the same for Linux and OS X, except we need to adapt the section names, so that the dynamic linker can generate symbols for these custom sections at load time. The section names must not have a period in their name and the symbols follow a certain naming convention, all of which are different for these platforms:

| platform | section name | symbol names |
|:-:|:-:|:-:|
| AVR <br/> Cortex-M | `".assertion"` | `__assertion_table_start` <br/> `__assertion_table_end` |
| OS X | `"__DATA,xpcc_assertion"` | `"section$start$__DATA$xpcc_assertion"`<br/>`"section$end$__DATA$xpcc_assertion"` |
| Linux | `"xpcc_assertion"` | `__start_xpcc_assertion` <br/> `__stop_xpcc_assertion` |

To access the symbols on OS X you need to bind them to their assembly name:
```c
extern AssertionHandler __assertion_table_start __asm("section$start$__DATA$xpcc_assertion");
extern AssertionHandler __assertion_table_end __asm("section$end$__DATA$xpcc_assertion");
```

**3 Feb 2018 -- Update:** We define some default assertion handlers inside the xpcc library source, which is first compiled into the `libxpcc.a` archive, then linked against by the application. However, the linker by default only searches archives for *referenced* symbols, which our handlers are obviously not, and therefore these handlers are omitted from the final executable. This can cause some very subtle and annoying bugs!

The solution is to wrap the archive in `-Wl,--whole-archive -lxpcc -Wl,--no-whole-archive`. The [GNU ld documentation](https://sourceware.org/binutils/docs/ld/Options.html#Options) describes this quite well: "For each archive mentioned on the command line after the `--whole-archive` option, include every object file in the archive in the link, rather than searching the archive for the required object files."

Note that this just makes all symbols *visible* to the linker, it does not force inclusion of all symbols, especially not if you pass the `--gc-sections` option as well.


#### AVRs are annoying

The most pain was getting this to work on AVRs though. The issue is that their address space is limited to 16-bit and instructions and data are placed into physically separate memories each with their own 16-bit address space. Or in other words, [AVRs implement a Harvard architecture](https://en.wikipedia.org/wiki/Harvard_architecture) and one does not simply read data from the instruction memory on a Harvard architecture. AVRs load their read-only data from Flash to SRAM at boot time, *including all strings*, since there is no way of telling from a 16-bit address whether it points to the instruction or the data memory. Hey, don't look at me, it's a 8-bit CPU, you get what you pay for!

This does, however, mean that there now need to be two versions of the same section in memory. GNU ld deals with this by allowing to specify two addresses per section: [the virtual address (VMA) and the load address (LMA)](https://sourceware.org/binutils/docs/ld/Output-Section-LMA.html).
For read-only data the LMA is in Flash somewhere, while the VMA is in SRAM and they are both *different* memories even when the section addresses overlap numerically!

Let me illustrate the problem with a simplified excerpt of the linkerscript itself.
You can see the `.data` section is appended onto the `text` memory after the `.text` section (LMA), but placed into the `data` memory too (VMA):
```ld
MEMORY
{
    text   (rx)   : ORIGIN = 0, LENGTH = 8k
    data   (rw!x) : ORIGIN = 0x800060, LENGTH = 0xffa0
}
/* everything in Flash */
.text :
{
    *(.progmem*) /* things tagged with `PROGMEM` go here! */
    *(.text*)    /* the actual code */
} > text
/* everything in SRAM */
.data :
{
    *(.data*)    /* modifiable data */
    *(.rodata*)  /* read-only data */
} > data AT> text
```
This is shown more obviously in the listing of the linked executable:
```
Sections:
Idx Name            Size      VMA       LMA       File off  Algn
  0 .text           00000850  00000000  00000000  000000b4  2**1
  1 .data           00000014  00800100  00000850  00000904  2**0
```

So what we need to do is simply™ append our section to the `text` memory after the `.data` section, right? Well…
`avr-gcc` uses its own linkerscripts (which can be found in `avr-binutils/avr/lib/ldscripts`), so we cannot just add our custom section as we did for the ARM platform.
Fortunately, GNU ld allows to extend default linkerscript using the [`INSERT [ AFTER | BEFORE ] output_section` command](https://sourceware.org/binutils/docs/ld/Miscellaneous-Commands.html).
We can pass this script to `avr-ld` via the `-T` option:
```
SECTIONS
{
    .xpcc_assertion : ALIGN(2)
    {
        __assertion_table_start = .;
        KEEP(*(.assertion))
        __assertion_table_end = .;
    }
}
INSERT AFTER .data
```
This places the section exactly where we want it:
```
Sections:
Idx Name            Size      VMA       LMA       File off  Algn
  0 .text           00000850  00000000  00000000  000000b4  2**1
  1 .data           00000014  00800100  00000850  00000904  2**0
  2 .xpcc_assertion 00000006  00000864  00000864  00000918  2**1
```

The code for `xpcc_assert_fail` also needs to be adapted for reading from Flash:
```c++
// use *_P string functions from <avr/pgmspace.h>
const char * module = identifier;
const char * location = module + strlen_P(module) + 1;
const char * failure = location + strlen_P(location) + 1;
// we can't access the function pointer directly, cos it's not in RAM
AssertionHandler * table_addr = &__assertion_table_start;
for (; table_addr < &__assertion_table_end; table_addr++)
{
    // first fetch the function pointer from flash, then jump to it
    AssertionHandler handler = (AssertionHandler) pgm_read_word(table_addr);
    state |= handler(module, location, failure, context);
}
```

Well, that was easy. This code works fine until the AVR `.text + .data` section size gets so large that it pushes the `.xpcc_assertion` section above the 64kB address boundary (AVRs can have up to 128kB Flash, don't ask /o\\). Then `table_addr` would wrap around and read garbage. For us this is an acceptable caveat. I mean, if you really get to *that* point, you should sit down and ask yourself some hard questions about your life.

## The Evaluation

So what are the properties of our solution?

### Overhead

Our assertions are a simple concept, with a very low overall code size overhead and when the assertion succeeds also low execution time penalty, even on AVRs.
There is obviously an unavoidable overhead for checking the test condition, safety doesn't come for free.
But what is the code size penalty per assertion in the code? We'll benchmark using this assertion:
```c
xpcc_assert(timeout > 0, "can", "init", "timeout", 1);
```

In AVRs, the assembly shows a simple condition check, a branch over for when the assertion passes, otherwise 4 loads and a call to `xpcc_assert_fail`:
```asm
2e4:   81 11       cpse r24, r1    ; condition check
2e6:   05 c0       rjmp .+10       ; branch over
2e8:   60 e0       ldi  r22, 0x01  ; context is 16-bit
2ea:   70 e0       ldi  r23, 0x00  ; constant and 1
2ec:   83 ea       ldi  r24, 0xA3  ; load ptr to progmem string
2ee:   90 e0       ldi  r25, 0x00  ; progmem below text, hence 0 here
2f0:   5d d1       rcall   .+698   ; call <xpcc_assert_fail>
```

On ARMv7-M the assembly is a little different. The simple condition check branches over if the assertion passes, otherwise `mov`es and loads the two arguments before loading and calling `xpcc_assert_fail`:
```asm
80001ca:   f003 01ff and.w r1, r3, #255  ; condition check
80001cc:   b913      cbnz  r3, 80001d8   ; branch over
80001d0:   2100      movs  r1, #1        ; context is constant and 1
80001d2:   4803      ldr   r0, [pc, #12] ; load value @ 80001e0
80001d4:   4b03      ldr   r3, [pc, #12] ; load value @ 80001e4
80001d6:   4798      blx   r3            ; call <xpcc_assert_fail>
...                                      ; hey look, a literal pool
80001e0:   08000d8c  .word   0x08000d8c  ; pointer to string
80001e4:   08000521  .word   0x08000521  ; pointer to function
```

The minimal code overheads per assertion call are 14B on AVR and 20B on ARMv7-M, but depending on the complexity of the test condition, more code can be generated.
However, if an assertion fails a time penalty exists: All assertion handlers will be called always. Furthermore everything executes on the currently active stack, maybe we'll change that in the future.

### Atomicity

A failed assert disables interrupts since its implementation is not reentrant!
Also keep in mind that our ARMv7-M HardFault handler also eventually calls `xpcc_assert_fail` and due to its hardcoded priority, it cannot be interrupted anyway. So it's best to always have the same behavior everywhere.

The abandon handler may choose to re-enable interrupts if required, for example to allow the UART driver to print the failure reason.
Furthermore if mission critical systems need to continue running, then the abandon handler can keep them alive. For us this would include maybe putting the robot in a mechanically safe configuration before shutting down the motor drivers.

### Nesting

Failing an assertion while already handling a failed assertion is not allowed and leads to an immediate termination (aka. an infinte loop). This can happen quicker than you think. Remember the abandon handler printing the failure over UART? What if the failure is the UART buffer overflowing? Yeah, that.

### Documentation

There is no way of knowing if the function you're calling can fail an assert, except from documentation. This can be a big issue, especially when inadvertently failing assertions from inside an interrupt context, which would call all assertion handlers and the abandon handler from this context too.

This is a difficult problem to fix in general, but it doesn't need to be solved perfectly: The application could be compiled in "assertion debug mode" where every assertion calls an "awareness" handler regardless of the test condition. This could also help with profiling assertion usage.

### Ignoring Assertions

It is a bit weird that contrary to C++ exceptions, the caller cannot handle the assertion directly at the call site, but only globally.
We tried to make it easier by allowing declarations of global assertion handlers anywhere, so that they can at least be declared closer to the call site.
But if you ignore an assertion, execution will continue, and there is no way to let the caller know that an assertion occurred, except to set a flag in shared memory:
```c++
static bool assertion_failed = false;
Abandonment ignore_uart_buffer(char *, char *, char *, uintptr_t)
{
    if (!strncmp(module, "uart", 4)) {
        assertion_failed = true;
        return xpcc::Abandonment::Ignore;
    }
    return xpcc::Abandonment::DontCare;
}
XPCC_ASSERTION_HANDLER(ignore_uart_buffer);

void caller_function(void)
{
    call_function_with_assertion();
    if (assertion_failed) {
        assertion_failed = false;
        // do something else
    }
}
```
Admittedly, this is an edge case and the vast amount of assertion failures cannot be ignored, as there is nothing the caller can do and abandonment is exactly the right choice.

### Abandonment Causes

As food for thought, here are the causes of abandonment in Midori and the possible implementations in xpcc. Note that AVRs don't have fault handlers, they just quietly choke on their bits until they die in a plume of blue smoke.

| bug description | xpcc implementation |
|:-|:-|
| An incorrect cast | undetectable at runtime |
| An attempt to dereference a `null` pointer | Hard Fault or unpredictable (AVR) |
| An attempt to access an array outside of its bounds | detectable only with wrapper code |
| Divide-by-zero | Hard Fault or `xpcc_assert` (software) |
| An unintended mathematical over/underflow | detectable only with wrapper code |
| Out-of-memory | `xpcc_assert` in dynamic allocator |
| Stack overflow | Hard Fault or undetectable (AVR) |
| Explicit abandonment | `xpcc_assert(false, ...)` |
| Contract failures | not a part of C/C++ (sadly) |
| Assertion failures | uh, well, `xpcc_assert` |

## The Conclusion

Our solution isn't anywhere near as polished and well thought out as Midori's, but considering our restrictions it's not completely terrible.
I would claim that it works for enough of our use cases to be useful and it allows for a lot of flexibility in responding to failed assertions.
Our approach of encoding the failure as a string is novel in the context of microcontrollers and is very efficient too.

We see this as a good enough alternative to C++ exceptions and will be using it a lot in xpcc.
