import sounddevice as sd


def main() -> None:
    devices = sd.query_devices()
    print("Input devices:\n")
    for idx, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) <= 0:
            continue
        name = dev.get("name", "")
        chans = dev.get("max_input_channels", 0)
        sr = dev.get("default_samplerate", "?")
        print(f"[{idx}] {name} (inputs={chans}, default_sr={sr})")

    print("\nOutput devices:\n")
    for idx, dev in enumerate(devices):
        if dev.get("max_output_channels", 0) <= 0:
            continue
        name = dev.get("name", "")
        chans = dev.get("max_output_channels", 0)
        sr = dev.get("default_samplerate", "?")
        print(f"[{idx}] {name} (outputs={chans}, default_sr={sr})")


if __name__ == "__main__":
    main()
