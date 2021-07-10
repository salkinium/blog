import matplotlib.pyplot as plt
from pathlib import Path
from collections import defaultdict

localpath = lambda path: Path(__file__).parent / path

DATA = defaultdict(lambda: defaultdict(dict))

def read_tables(glob_filter):
    global DATA
    for file in localpath(".").glob(glob_filter):
        device = file.name[5:-4]
        print(device)
        tables = file.read_text().split("\n\n")
        for table in tables:
            data = parse_table(table)
            DATA[device][data[0]][data[1]] = data[2]

def parse_table(text):
    lines = text.strip().splitlines()
    clock = int(lines[0].split(" = ")[1])
    dtype = lines[0][12:14]
    def conv(delay, cycles):
        return (cycles * (1e9 if dtype == "ns" else 1e6)) / clock;
    data = []
    for line in lines[3:]:
        values = [int(v.strip()) for v in line.rstrip(">").split(" | ")]
        data.append( (values[0], conv(values[0], values[2]), values[2]) )

    return (dtype, clock, data)


def plot_table(device_filter, dtype_filter, clock_filter, delay_filter):
    global DATA
    for device, dtypes in DATA.items():
        if not device_filter(device): continue;
        for dtype, clocks in dtypes.items():
            if not dtype_filter(dtype): continue;
            for clock, delays in clocks.items():
                if not clock_filter(clock): continue;
                print(device, dtype, clock)
                filtered = [d for d in delays if delay_filter(d)]
                plt.plot([v[0] for v in filtered], [v[1] for v in filtered])

def dump_summary():
    global DATA
    for device, dtypes in DATA.items():
        minmax_clock = (1e9, 0)
        min_cycles_boot = 1e9
        min_cycles_high = 1e9
        for dtype, clocks in dtypes.items():
            for clock, delays in clocks.items():
                minmax_clock = (min(minmax_clock[0], clock), max(minmax_clock[1], clock))
                min_cycles = min(d[2] for d in delays)
                if clock <= 16e6:
                    min_cycles_boot = min(min_cycles_boot, min_cycles)
                else:
                    min_cycles_high = min(min_cycles_high, min_cycles)
        print("| {} | {}/{} | {}ns @ {} MHz | {}ns @ {} MHz |".format(
                device, min_cycles_boot, min_cycles_high,
                int(min_cycles_boot * 1e9 / minmax_clock[0]), minmax_clock[0] / 1e6,
                int(min_cycles_high * 1e9 / minmax_clock[1]), minmax_clock[1] / 1e6))


if __name__ == "__main__":
    read_tables("data_*")

    dump_summary()

    # Large figure
    plt.figure(figsize=(20, 10))
    plt.axline((0, 0), (10000, 10000), color="gray")
    plot_table(lambda d: True,
               lambda t: t == "ns",
               lambda c: c <= 16e6,
               lambda d: d[0] <= 10000)
    plt.xticks(fontsize=14); plt.yticks(fontsize=14)
    plt.xlabel("Input nanosecond delay", fontsize=16)
    plt.ylabel("Measured nanosecond delay", fontsize=16)
    plt.text(100, 7900, "STM32L0/L1 @ ~2MHz", fontsize=16)
    plt.annotate("STM32F7 @ 16MHz", xy = (4000, 4800), fontsize=16, ha='right', va='bottom',
                 xytext=(3500, 5200), arrowprops = {"arrowstyle": "-"})
    plt.annotate("AVR @ 16MHz", xy = (5125, 5640), fontsize=16, ha='right', va='bottom',
                 xytext=(4800, 6200), arrowprops = {"arrowstyle": "-"})
    plt.annotate("STM32F1 @ 8MHz", xy = (2480, 2000), fontsize=16, ha='left', va='top',
                 xytext=(3500, 2000), arrowprops = {"arrowstyle": "-"})
    plt.annotate("STM32L4 @ 16MHz", xy = (910, 812), fontsize=16,
                 xytext=(1500, 500), arrowprops = {"arrowstyle": "-"})
    plt.annotate("Ideal", xy = (400, 400), fontsize=16,
                 xytext=(600, -100), arrowprops = {"arrowstyle": "-"})
    plt.savefig("ns_boot.svg", transparent=True, bbox_inches='tight')

    plt.clf()
    plt.axline((0, 0), (1000, 1000), color="gray")
    plot_table(lambda d: True,
               lambda t: t == "ns",
               lambda c: c > 16e6,
               lambda d: d[0] <= 1000)
    plt.xticks(fontsize=14); plt.yticks(fontsize=14)
    plt.xlabel("Input nanosecond delay", fontsize=16)
    plt.ylabel("Measured nanosecond delay", fontsize=16)
    plt.annotate("Ideal", xy = (50, 50), fontsize=16,
                 xytext=(50, 0), arrowprops = {"arrowstyle": "-"})
    plt.text(10, 545, "STM32L0/L1 @ 32MHz", fontsize=16)
    plt.text(10, 412, "STM32F0 @ 48MHz", fontsize=16)
    plt.text(10, 330, "STM32L4 @ 48MHz", fontsize=16)
    plt.annotate("STM32F7 @ 216MHz", xy = (180, 230), fontsize=16, ha='left', va='top',
                 xytext=(280, 190), arrowprops = {"arrowstyle": "-"})
    plt.annotate("STM32F4 @ 180MHz", xy = (99, 88), fontsize=16, ha='left', va='top',
                 xytext=(190, 80), arrowprops = {"arrowstyle": "-"})
    plt.savefig("ns_high_detail.svg", transparent=True, bbox_inches='tight', pad_inches=0.01)
    # plt.show()

    plt.clf()
    # Smaller figure
    plt.figure(figsize=(20, 8.1))
    plot_table(lambda d: True,
               lambda t: t == "ns",
               lambda c: c > 16e6,
               lambda d: d[0] <= 10000)
    plt.xticks(fontsize=14); plt.yticks(fontsize=14);
    plt.xlabel("Input nanosecond delay", fontsize=16)
    plt.ylabel("Measured nanosecond delay", fontsize=16)
    plt.annotate("STM32F7 @ 216MHz", xy = (8000, 7500), fontsize=16, ha='left', va='top',
                 xytext=(8000, 6500), arrowprops = {"arrowstyle": "-"})
    plt.savefig("ns_high.svg", transparent=True, bbox_inches='tight', pad_inches=0.01)

    plt.clf()
    plot_table(lambda d: True,
               lambda t: t == "us",
               lambda c: c <= 16e6,
               lambda d: d[0] <= 1000)
    plt.xticks(fontsize=14); plt.yticks(fontsize=14)
    plt.xlabel("Input microsecond delay", fontsize=16)
    plt.ylabel("Measured microsecond delay", fontsize=16)
    plt.savefig("us_boot.svg", transparent=True, bbox_inches='tight', pad_inches=0.01)




