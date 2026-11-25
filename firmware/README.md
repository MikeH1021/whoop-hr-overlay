# USB dongle firmware (nRF52840 Dongle, PCA10059)

`hci_usb_nrf52840dongle_dfu.zip` turns a Nordic nRF52840 USB dongle into a plain Bluetooth LE
controller. It shows up on USB as **"WHOOP HR Overlay HCI"**, USB ID `2FE3:000B`. The overlay
drives it directly with [Bumble](https://github.com/google/bumble), bypassing the PC's built-in
Bluetooth. Use it when the PC's own radio is too weak to hear the band.

Verified on hardware: the dongle hears the band at about −47 dBm (the test PC's onboard radio
heard it at −101), and the overlay reads live HR from the band's standard heart-rate channel.
That channel needs **no pairing**, so the band stays bonded only to your phone.

## What's in the build (`hci_usb_failsafe/`)
Zephyr 3.7 Bluetooth HCI-over-USB, with four changes. Without them, this dongle never
enumerates:

| Setting | Why |
|---|---|
| `CONFIG_BOARD_ENABLE_DCDC=n`, `..._DCDC_HV=n` | Use the LDO regulator. With DCDC on, the dongle browns out and reboot-loops as soon as USB and the radio start. |
| `CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y` | The dongle has no 32.768 kHz crystal; the default build waits forever for it. |
| `CONFIG_USB_DEVICE_STACK_NEXT=y`, `CONFIG_BOARD_SERIAL_BACKEND_CDC_ACM=n` | Use the new USB stack only. The board's CDC console otherwise pulls the legacy stack in as well. |
| Fail-safe (`src/main.c`) | At boot it requests DFU mode (GPREGRET `0xB1`) and arms an 8 s watchdog. It cancels both only once a USB host resets the bus. If a future image hangs, the dongle drops back into the bootloader by itself, so **no button needed** to reflash it. |

LEDs: LED1 green = booted; LED2 blue = starting USB; LED2 green = a USB host sees it;
LED2 red = USB stack failed.

## Flash it
1. **Enter the bootloader:** unplug the dongle, **hold its side button**, plug it in while
   holding, release. The red LED pulses slowly. (Tapping the button while it's running does
   *not* enter the bootloader.) You only need this the first time: once this firmware is on,
   a failed image re-enters the bootloader by itself.
2. Install [nRF Util](https://www.nordicsemi.com/Products/Development-tools/nRF-Util), then
   `nrfutil install nrf5sdk-tools`.
3. Flash (the dongle's COM port on Windows, `/dev/cu.usbmodem…` on macOS):
   ```
   nrfutil nrf5sdk-tools dfu usb-serial -pkg hci_usb_nrf52840dongle_dfu.zip -p COM5
   ```

## Windows driver (one time)
The overlay talks to the dongle through libusb, which needs the **WinUSB** driver:
1. Download [Zadig](https://zadig.akeo.ie/), plug in the dongle, run Zadig.
2. *Options → List All Devices*, pick **WHOOP HR Overlay HCI** (USB ID `2FE3 000B`).
3. Target driver **WinUSB** → *Replace Driver* (or *Install Driver*).

Then the overlay uses the dongle automatically. The tray menu shows **Bluetooth: USB dongle**.

## Go back to the sniffer
Enter the bootloader (hold the button while plugging in) and flash Nordic's sniffer package:
```
nrfutil nrf5sdk-tools dfu usb-serial -pkg sniffer_nrf52840dongle_4.1.1_dfu.zip -p COM5
```

## Rebuild
```
west init -m https://github.com/zephyrproject-rtos/zephyr --mr v3.7.0 .
west config manifest.project-filter -- '-.*,+cmsis,+hal_nordic,+tinycrypt'
west update
west build -b nrf52840dongle/nrf52840 path/to/firmware/hci_usb_failsafe
nrfutil nrf5sdk-tools pkg generate --hw-version 52 --sd-req 0x00 \
  --application build/zephyr/zephyr.hex --application-version 5 hci_usb_nrf52840dongle_dfu.zip
```
