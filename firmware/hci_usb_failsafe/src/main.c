/*
 * Bluetooth HCI over USB for the nRF52840 Dongle (PCA10059), with a fail-safe:
 *
 *   At the very start of boot we request "DFU mode" from the nRF5 bootloader
 *   (GPREGRET = 0xB1) and start the hardware watchdog (8 s). Only once a USB
 *   host has actually reset/configured us do we clear the request and start
 *   feeding the watchdog. So if this firmware ever hangs, faults or reboot-
 *   loops before USB works, the next reset lands in the bootloader and the
 *   dongle can be reflashed without holding its button.
 *
 * LEDs: LED1 green = booted; LED2 blue = in main; LED2 green = USB host seen;
 *       LED2 red = USB stack failed to start.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <zephyr/kernel.h>
#include <zephyr/init.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/usb/usbd.h>
#include <sample_usbd.h>
#include <hal/nrf_power.h>
#include <nrfx.h>

#define DFU_MAGIC 0xB1

static const struct gpio_dt_spec boot_led = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);
static const struct gpio_dt_spec red = GPIO_DT_SPEC_GET(DT_ALIAS(led1), gpios);
static const struct gpio_dt_spec green = GPIO_DT_SPEC_GET(DT_ALIAS(led2), gpios);
static const struct gpio_dt_spec blue = GPIO_DT_SPEC_GET(DT_ALIAS(led3), gpios);

static void led(const struct gpio_dt_spec *l, int on)
{
	gpio_pin_configure_dt(l, on ? GPIO_OUTPUT_ACTIVE : GPIO_OUTPUT_INACTIVE);
}

/* ---- fail-safe ---------------------------------------------------------- */
static int failsafe_arm(void)
{
	NRF_POWER->GPREGRET = DFU_MAGIC;         /* next soft/WDT reset -> DFU */
	NRF_WDT->CONFIG = (WDT_CONFIG_SLEEP_Run << WDT_CONFIG_SLEEP_Pos);
	NRF_WDT->CRV = 8 * 32768;                /* 8 seconds */
	NRF_WDT->RREN = WDT_RREN_RR0_Msk;
	NRF_WDT->TASKS_START = 1;
	return 0;
}
SYS_INIT(failsafe_arm, PRE_KERNEL_1, 0);

static void wdt_feed(struct k_timer *t)
{
	ARG_UNUSED(t);
	NRF_WDT->RR[0] = WDT_RR_RR_Reload;
}
K_TIMER_DEFINE(feed_timer, wdt_feed, NULL);

static atomic_t usb_ok;

static void usb_healthy(void)
{
	if (atomic_set(&usb_ok, 1) == 0) {
		NRF_POWER->GPREGRET = 0;         /* working: normal boots from now */
		k_timer_start(&feed_timer, K_NO_WAIT, K_SECONDS(1));
		led(&green, 1);
		led(&blue, 0);
	}
}

static void msg_cb(struct usbd_context *const ctx, const struct usbd_msg *const msg)
{
	ARG_UNUSED(ctx);
	if (msg->type == USBD_MSG_RESET) { /* a host reset the bus: it sees us */
		usb_healthy();
	}
}

/* ---- boot ---------------------------------------------------------------- */
static int boot_mark(void)
{
	led(&boot_led, 1);
	return 0;
}
SYS_INIT(boot_mark, POST_KERNEL, 99);

int main(void)
{
	led(&blue, 1);

	struct usbd_context *ctx = sample_usbd_init_device(msg_cb);

	if (ctx == NULL || usbd_enable(ctx) != 0) {
		led(&blue, 0);
		led(&red, 1);
		return 0; /* watchdog will drop us into the bootloader */
	}
	return 0;
}
