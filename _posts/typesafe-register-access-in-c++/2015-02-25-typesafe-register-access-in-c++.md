---
layout: post
title: Typesafe Register Access in C++
---

When you are writing software for microcontrollers, reading and writing hardware registers becomes second nature.
Registers and bit mappings are typically "modeled" using C preprocessor defines, and usually provided to you by your cross compiler toolchain in device specific header files.

Setting up and toggling PG13 on the STM32F4 this way looks rather... unreadable:

```cpp
// set push-pull, output
GPIOG->OSPEEDR = (GPIOG->OSPEEDR & ~(3 << 26)) | (3 << 26);
GPIOG->MODER   = (GPIOG->MODER & ~(3 << 26)) | (1 << 26);
GPIOG->OTYPER &= ~(1 << 13);
GPIOG->PUPDR  &= ~(1 << 13);

while(true)
{
    GPIOG->ODR ^= (1 << 13);  // toggle
    // delay
}
```

It did not really dawn on me how primitive this concept was until I was forced to model a memory map myself for one of our many [device drivers](http://xpcc.io/api/group__driver.html).
Since I have never been a friend of using the C preprocessor in C++ unless absolutely necessary, it seemed like a good opportunity to research how best to implement this in pure C++.

<!--more-->

## Existing Concepts

Martin Moene has compiled [an excellent  overview](http://www.eld.leidenuniv.nl/~moene/Home/papers/accu/overload95-register/) of the relevant publications regarding C++ hardware register access.
Perhaps the most relevant of those is a paper written by Ken Smith titled ["C++ Hardware Register Access Redux"](http://yogiken.files.wordpress.com/2010/02/c-register-access.pdf) written in 2010.

Smith's policy based design is quite complete and there even exists a [functioning implementation](https://github.com/JinShil/memory_mapped_io) by Jin Shil keyed to embedded systems --- albeit in the D programming language.[^1]
The author has implemented part of the STM32F4 memory map with these classes and uses them in a simple [D program for toggling a pin][stm32f4_d] (a C++ version would look similar):

```d
// set push-pull, output
GPIOG.OSPEEDR.OSPEEDR13.value = 3;
GPIOG.MODER.MODER13.value     = 1;
GPIOG.OTYPER.OT13.value       = 0;
GPIOG.PUPDR.PUPDR13.value     = 0;

while(true)
{
    GPIOG.ODR.ODR13.value = !GPIOG.ODR.ODR13.value;    // toggle
    // delay
}
```

**4 Mar 2015 -- Update:** I seem to have completely missed [Ken Smith's own implementation][cppmmio]. He also has a [Cortex-M example](https://github.com/kensmith/cortex-from-scratch).<br/>
**5 Mar 2015 -- Update:** For an excellent example of a similar implementation for the AVR using C++11, see [the yalla library](https://github.com/chrism333/yalla).<br/>
**8 Sep 2015 -- Update:** There also exists the [Kvasir register implementation](https://github.com/kvasir-io/Kvasir/tree/master/Lib/Register) which has a few more tricks up its sleeve regarding atomicity and efficiency. Impressive.

Here are a few of my observations:

1. How does one generate the C++ memory map? By hand for every device? How do you keep it up-to-date for new devices?
2. The above code is syntactically already a huge improvement. However, its semantics are just as cryptic. What does writing value `3` into register `GPIOG.OSPEEDR.OSPEEDR13` actually mean?
3. The papers compiled by Moene are assuming we want to access the devices internal memory. For devices connected via an external bus, accessing a memory location can be an expensive operation, which can take a long time and even fail.


#### High Effort Solution

Even though Smith's policy based design does not carry a runtime penalty, implementing and maintaining it clearly comes with some overhead.
Every device family requires its own memory map implementation, which would be crazy to code by hand. Not only for the sheer effort required, but also since this would be incredibly prone to errors. ([Jin Shin seems to have realized this too](https://github.com/JinShil/stm32_datasheet_to_d).)
If the manufacturer adds a new device to the family, you would have to update and perhaps extend this memory map implementation.

A generator is required, which converts a computer readable memory map into the C++ counterpart and this with as little additional effort as possible.
Notice that the existing memory files in your compiler toolchain (the ones with the defines) only provide you with names for memory addresses, bits and configuration, but lacks the information required for generating the correct policies.

Therefore you would need to find an "annotated" memory map, which is probably only available directly from the manufacturer. Atmel has so-called "Part Description Files" hidden somewhere deep in AVR Studio, which are used by the avr-gcc developers to generate the `io.h` memory files for the AVRs. ST Microelectronics has similar files hidden in their STM32Cube initialization code generator.

I know this, because xpcc uses exactly these memory maps to generate its [own device files](https://github.com/roboterclubaachen/xpcc/tree/develop/src/xpcc/architecture/platform/devices). I can tell you that writing and maintaining a parser for these files is painful, since they are littered with inconsistencies. I cannot imagine maintaining this for the entire memory map. The errors in Atmel's memory maps drive me crazy enough.

So unless the manufacturer goes the extra mile and publishes their device memory maps preferably open-source on GitHub (unlikely) or directly provide the C++ implementations themselves (even less likely), the burden to generate these implementations and keep them up-to-date is placed on the library maintainers.
That's not really an option.

**17 Mar 2015 -- Update:** As part of [CMSIS](http://cmsis.arm.com), ARM has standardized [System View Description (SVD)](http://www.keil.com/pack/doc/CMSIS/SVD/html/index.html) files which describe the memory map of vendor devices. These files could be the foundation for generation, however, a vendor-specific EULA needs to be agreed to before download.<br/>
**12 Sep 2015 -- Update:** Paul has created a [GitHub repository containing most SVD files and a python parser](https://github.com/posborne/cmsis-svd). This could be a gamechanger!

#### Semantics Matter

Let me put forward this blunt theory:
Regardless of how elegantly register access is realized in a language, there is hardly a semantical advantage of this, since you are still writing magic numbers into a lot of magic registers (in a more beautiful and type-safe way though).

In xpcc the above code is reduced to these three equivalent *and* self-explanatory lines:

```cpp
GpioG13::configure(Gpio::OutputType::PushPull);
GpioG13::setOutput();

while(true)
{
    GpioG13::toggle();
    // delay
}
```

Notice how there are no registers in this code whatsoever and how clear the meaning of the code becomes.
It seems to be much more useful to implement a clean hardware abstraction layer, than a form of register access.

This does not mean that elegant register access is pointless, however, its level of abstraction might be too low for your library to benefit from.
It might be an enrichment for the library developers (Smith proposes unit testing register access), but usually a higher level of abstraction is required.


#### Missed The Bus

An external device is typically connected through a serial bus like UART, SPI or I<sup>2</sup>C, which is very slow compared to any internal bus.
Even the few devices using a parallel bus interface (like external RAM) most often multiplex their address and data lines to minimize the amount of required pins. It's probably fair to say "Internal > Parallel > SPI > UART > I<sup>2</sup>C" in terms of transfer speed.

Since a typical read-modify-write means accessing the bus twice, a naive implementation of our example code would yield 8 bus accesses for setting up the port and another 2 for every pin toggle.
That only takes a few cycles using the internal bus[^2], but would easily stretch to micro- and milliseconds on an external serial bus, which makes it impractical to busy-wait during this time.

There is another problem. Consider the following memory layout of an external accelerometer:

```
rw: | Config1 | Config2 | Config3 | Config4 |
       0x20      0x21      0x22      0x23

ro: | Status | XL | XH | YL | YH | ZL | ZH |
       0x30   0x31 0x32 0x33 0x34 0x35 0x36
```

Both the configuration registers as well as the read-only registers are all placed in one continuos memory block.
The most usual serial interfaces of external devices auto-increment their start address, so that we can efficiently write or read a continuos block of memory.
This would allow us one bus access to write the four configuration bytes, and not have to access the bus four times to write only one byte each time.
Similarly, we also do not want to access each of the read-only registers separately.

However, such a register block access is not considered in Smith's design nor in Jin Shin's implementation.
It is also "merely" an optimization when using a different bus type, but it still breaks with the existing interface.

Considering this and the ideas on semantics, I would argue that devices connected through an external bus require a different level of abstraction.


## What Now?

If you have read to here the situation seems a bit hopeless.
The existing solutions are difficult to implement and maintain, provide little semantical advantages and do not work well over external busses.
And I still have no idea how to model the memory map of my external devices.

*So let us ignore the internal memory.* We already have a way of using it with the defines and with a good hardware abstraction layer there should be no need to access them directly.


#### Modelling Registers

Instead, let's focus on how to model register content of external devices and ignore the bus for the moment.

Registers can be made up of three things:

- Bits: a single bit (position *N*),
- Configurations: a combination of bits where the meaning does not correspond to its numeric value (position *[N, M]*)
- Values: a numeric value (position *[N, M]*)

Example of an 8bit register: Control

![Control Register](control_register.svg)

- Bit *7*: Enable
- Bit *6*: Full Scale
- Configuration *[5, 4]*: Prescaler
	- 00: Divide by 1
	- 01: Divide by 2
	- 10: Divide by 4
	- 11: Divide by 8
- Value *[3, 1]*: Start-Up Delay in ms

There should be an easy way to access all of this information in the register.


#### static constexpr

The first idea and implementation was a bit messy.
I wanted to make every bit a static constant expression of class `Bit`.
Similar constructs are possible for configurations and values.
Using operator and constructor overloading these constant expressions could be converted and assigned and OR'ed in a type-safe way.

```cpp
struct Control : public Register8
{
	static constexpr Bit EN  = Bit7;
	static constexpr Bit FS  = Bit6;

	struct Prescaler : public Group
	{
		static constexpr Type BitPosition = 4;
		static constexpr Config Mask = 0b11 << BitPosition;

		static constexpr Config DivideBy1 = 0 << BitPosition;
		static constexpr Config DivideBy2 = 0x01 << BitPosition;
		static constexpr Config DivideBy4 = 0x02 << BitPosition;
		static constexpr Config DivideBy8 = 0x03 << BitPosition;
	}
	...
}
```

I actually [implemented most of this][static_constexpr_registers] (with a bunch of ugly macros to reduce the verbosity of it).
Then I realized that `static constexpr` members require an external instantiation for the linker, which would place them somewhere in memory.
This is because the C++11 standard permits taking the address of a static constexpr member, and only instantiated members actually have an address.

What a dealbreaker.

**30 Aug 2015 -- Update:** Using a better approach, C. Biffle has implemented `Bitfields`, which models [memory-mapped register banks for his ETL library](https://github.com/cbiffle/etl/blob/master/biffield/README.mkdn).


## Strongly-Typed Enumerations

Which C++ type does not need to be instantiated to be used? Yes, enums.
However, C++03 enums convert to integers pretty quickly, but thankfully, in C++11 we have strongly-typed enums which don't do that.


#### Register Bits

Using strongly-typed enums we can describe the bits of the example register as such:

```cpp
enum class Control : uint8_t
{
	EN = Bit7,	///< bit documentation
	FS = Bit6,

	PRE1 = Bit5,
	PRE0 = Bit4,

	DEL2 = Bit3,
	DEL1 = Bit2,
	DEL0 = Bit1,
};
typedef Flags8< Control >  Control_t;
```

Since strongly-typed enums do not have any predefined operators, they are wrapped into the `Flags8` [template class][xpcc_flags][^3], which adds the necessary constructors and bitwise operator overloading to them and returns them as a `Flags8` type.[^4]

This means, you can handle all its register bits as you would expect:

```cpp
Control_t control = Control::EN;
control = Control::EN | Control::FS;
control &= ~Control::FS;
control |= Control::FS;
control ^= Control::PRE1;
bool isSet = control & Control::FS;

control.reset(Control::PRE1 | Control::PRE0);
control.set(Control::DEL0);

bool noneSet = control.none(Control::PRE1 | Control::PRE0);
bool allSet = control.all(Control::EN | Control::FS);
```

You still get raw access if you really need it:

```cpp
uint8_t raw = control.value; // the underlying type
control.value = 0x24;
```

And the access is type-safe, you cannot use bits from two different registers:

```cpp
enum class Control2 : uint8_t
{
	DIS = Bit4,
	HS = Bit3,
};
typedef Flags8< Control2 >  Control2_t;

auto control = Control::EN | Control2::HS; // compile error
```

You can even overload functions on argument type now:

```cpp
void write(Control_t control);
void write(Control2_t control);

write(Control::EN | Control::FS);  // calls #1
write(Control2::DIS);              // calls #2
```

#### Register Configurations

Configurations are also described as a strongly-typed enum and then wrapped into the `Configuration` [template class][xpcc_configuration].

```cpp
enum class Prescaler : uint8_t
{
	Div1 = 0,				///< configuration documentation
	Div2 = Control::PRE0,
	Div4 = Control::PRE1,
	Div8 = Control::PRE1 | Control::PRE0,
};
typedef Configuration< Control_t, Prescaler, (Bit5 | Bit4) >  Prescaler_t;
```

The `Prescaler` enum values are already shifted in this example (hence the `(Bit5 | Bit4)` mask), however you can also declare the prescaler values non-shifted and let the wrapper shift it:

```cpp
enum class Prescaler : uint8_t
{
	Div1 = 0,
	Div2 = 1,
	Div4 = 2,
	Div8 = 3,
};
typedef Configuration<Control_t, Prescaler, 0b11, 4> Prescaler_t;
```

Why? If you have two or more configurations with the same selections in the same register,  you can simply add another one:

```cpp
typedef Configuration< Control_t, Prescaler, 0b11, 6 >  Prescaler2_t;
```

Configurations can be used inline:

```cpp
Control_t control = Control::EN | Prescaler_t(Prescaler::Div2);
Control_t control &= ~Prescaler_t::mask();
```


But do not have to:

```cpp
Prescaler_t::set(control, Prescaler::Div2);
Prescaler_t::reset(control);
Prescaler prescaler = Prescaler_t::get(control);
```


#### Register Values

Values are described using the `Value` [template class][xpcc_value] which masks and shifts the value as required.
In our example the value has a width of 3 bits and needs to be shifted 1 bit:

```cpp
typedef Value< Control_t, 3, 1 >  Delay_t;
```

This can be used the same way as the Configuration:

```cpp
Control_t control = Control::EN | Prescaler_t(Prescaler::Div2) | Delay_t(4);
Control_t control &= ~Delay_t::mask();

Delay_t::set(control, 7);
Delay_t::reset(control);
uint8_t delay = Delay_t::get(control);
```


#### Efficiency

These classes are using as much `constexpr` as possible, so constexpr constructors, constexpr operator overloading and constexpr methods.
This means whatever can be computed at compile time, will be computed at compile time.

```cpp
Control_t control = Control::EN | Prescaler_t(Prescaler::Div2) | Delay_t(4);
// is just fancy syntax sugar coating for
uint8_t control = 0xA4;
```

Of course if your Configuration or Value class has to extract a value at runtime, the masking and shifting will happen at runtime. Not all that surprising.


## What About The Bus?

The above code works on a copy of the register content in the hosts RAM.
To understand why this makes a lot of sense for external devices, consider the accelerometer memory map from previously:

```
rw: | Config1 | Config2 | Config3 | Config4 |
       0x20      0x21      0x22      0x23

ro: | Status | XL | XH | YL | YH | ZL | ZH |
       0x30   0x31 0x32 0x33 0x34 0x35 0x36
```

In our device driver we would reserve 4 bytes for buffering the configuration registers, 1 byte for the status register and 6 bytes for the data.

Usually, configuration registers are not changed by the external hardware itself, so you can modify the local copy of the configuration register and then only need to write the result once to the external hardware.
During device driver initialization you can also prepare all configuration registers and then write all 4 at once.

Similarly the status and data bytes can be read in one bus access and buffered locally for further computations.

At the very basic level, the driver needs to provide functions to update the registers content:

```cpp
bool        // bool because bus access can fail
updateControl(Control_t setMask, Control_t clearMask = Control_t(0xff));

Control_t
getControl();
{  return Control_t(rawBuffer[0]);  }

updateControl(Control::FS, Control::EN);
// is equivalent to
Control_t control = getControl();
control &= Control::EN;
control |= Control::FS;
updateControl(control);
```

However providing meaningful setters and getters makes your code much more usable:

```cpp
bool
enable()
{  return updateControl(Control::EN, Control_t(0));  }

bool
disable()
{  return updateControl(Control_t(0), Control::EN);  }

bool
setPrescaler(Prescaler prescaler)
{  return updateControl(prescaler, Prescaler_t::mask());  }

Prescaler getPrescaler()
{  return Prescaler_t::get(getControl());  }
```

For working examples of this concept have a look at the [ITG3200][], [LIS302][] and [LIS3DSH][] device drivers.
These drivers use [resumable functions][xpcc_rfs] to make bus access non-blocking.


## Conclusions

1. Don't bother with a pure C++ model of your internal memory.
2. Better invest the time in a useful hardware abstraction layer.
3. Buffer often accessed registers of external devices locally.
4. Use the typesafe C++ access classes for these registers as presented.
5. Be aware of the overhead of using an external bus.





[^1]: D seems to be a lot better suited for compile time evaluations than C++.
[^2]: This is different from dektop-class CPUs, where even the internal bus is magnitudes slower than the CPU.
[^3]: You actually need to use `XPCC_FLAGS8(Control)`, which expands to `typedef Flags8<Control> Control_t;` and some magic enum operator overloading.
[^4]: While researching for this post I discovered an almost identical [`flags` class on Github][enum_flags]. However, it is not written for embedded targets and has a slightly different field of application.

*This post was first published at blog.xpcc.io.*

[cppmmio]: https://github.com/kensmith/cppmmio

[stm32f4_d]: https://github.com/JinShil/stm32f42_discovery_demo/blob/0f355d63bd7823f593ef770db1703bc2cf3454a6/source/start.d#L253
[static_constexpr_registers]: https://github.com/roboterclubaachen/xpcc/commit/ef55cb32a57b129af8a068f5b6c043eac2512312#diff-ba8846bac2db804c7b7c4a5d477002a0R159

[enum_flags]: https://github.com/grisumbras/enum-flags

[xpcc_flags]: http://xpcc.io/api/structxpcc_1_1_flags.html
[xpcc_configuration]: http://xpcc.io/api/structxpcc_1_1_configuration.html
[xpcc_value]: http://xpcc.io/api/structxpcc_1_1_value.html

[itg3200]: http://xpcc.io/api/classxpcc_1_1_itg3200.html
[lis302]: http://xpcc.io/api/classxpcc_1_1_lis302dl.html
[lis3dsh]: http://xpcc.io/api/classxpcc_1_1_lis3dsh.html

[xpcc_rfs]: http://xpcc.io/api/group__resumable.html
