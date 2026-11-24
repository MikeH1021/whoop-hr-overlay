# USB dongle firmware (nRF52840 Dongle, PCA10059)

`hci_usb_nrf52840dongle_dfu.zip` turns a Nordic nRF52840 USB dongle into a plain
Bluetooth LE controller (Zephyr 3.7 `samples/bluetooth/hci_usb`, USB ID `2FE3:000B`).
The overlay drives it directly with [Bumble](https://github.com/google/bumble), bypassing the
PC's built-in Bluetooth. Use it when the PC's own radio is too weak to hear the band.

Built with the dongle's **internal RC 32 kHz clock** (`CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y`).
The dongle has no 32 kHz crystal, so the default build hangs before USB starts.

## Flash it
1. **Enter the bootloader:** unplug the dongle, **hold its side button**, plug it in while
   holding, release. The red LED pulses slowly. (Tapping the button while it's running does
   *not* enter the bootloader.)
2. Install [nRF Util](https://www.nordicsemi.com/Products/Development-tools/nRF-Util), then
   `nrfutil install nrf5sdk-tools`.
3. Flash (use the dongle's COM port on Windows, `/dev/cu.usbmodem…` on macOS):
   ```
   nrfutil nrf5sdk-tools dfu usb-serial -pkg hci_usb_nrf52840dongle_dfu.zip -p COM5
   ```

## Windows driver (one time)
The overlay talks to the dongle through libusb, which needs the **WinUSB** driver:
1. Download [Zadig](https://zadig.akeo.ie/), plug in the dongle, run Zadig.
2. *Options → List All Devices*, pick the dongle (`USB-DEV`, USB ID `2FE3 000B`).
3. Target driver **WinUSB** → *Replace Driver*.

The overlay then switches to the dongle automatically. The tray menu shows
**Bluetooth: USB dongle**.

## Go back to the sniffer
Enter the bootloader the same way and flash Nordic's sniffer package:
```
nrfutil nrf5sdk-tools dfu usb-serial -pkg sniffer_nrf52840dongle_4.1.1_dfu.zip -p COM5
```

## Rebuild
```
west init -m https://github.com/zephyrproject-rtos/zephyr --mr v3.7.0 .
west config manifest.project-filter -- '-.*,+cmsis,+hal_nordic,+tinycrypt'
west update
west build -b nrf52840dongle/nrf52840 zephyr/samples/bluetooth/hci_usb -- \
  -DCONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y
nrfutil nrf5sdk-tools pkg generate --hw-version 52 --sd-req 0x00 \
  --application build/zephyr/zephyr.hex --application-version 2 hci_usb_nrf52840dongle_dfu.zip
```
