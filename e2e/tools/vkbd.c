/*
 * Virtual keyboard for a Linux player (piCorePlayer). Creates a uinput keyboard and replays
 * commands read from stdin, one per line:
 *
 *   d <code>   key down        u <code>   key up        s <ms>   sleep
 *
 * Codes are Linux input event codes (linux/input-event-codes.h), e.g. 36 = J, 38 = L, 42 = left shift.
 * Build: zig cc -target arm-linux-musleabihf -static -O2 vkbd.c -o vkbd   (see ../README.md)
 */
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static int fd;

static void emit(int type, int code, int value)
{
    struct input_event ev;
    memset(&ev, 0, sizeof ev);
    ev.type = type;
    ev.code = code;
    ev.value = value;
    if (write(fd, &ev, sizeof ev) < 0)
        perror("write");
}

int main(void)
{
    struct uinput_setup setup;
    char op;
    int arg;

    fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
    if (fd < 0) {
        perror("open /dev/uinput");
        return 1;
    }
    ioctl(fd, UI_SET_EVBIT, EV_KEY);
    ioctl(fd, UI_SET_EVBIT, EV_SYN);
    for (int code = 1; code < 249; code++)
        ioctl(fd, UI_SET_KEYBIT, code);

    memset(&setup, 0, sizeof setup);
    setup.id.bustype = BUS_USB;
    setup.id.vendor = 0x1234;
    setup.id.product = 0x5678;
    strcpy(setup.name, "e2e virtual keyboard");
    ioctl(fd, UI_DEV_SETUP, &setup);
    ioctl(fd, UI_DEV_CREATE);
    usleep(500000); /* let the reader notice the new device */

    while (scanf(" %c %d", &op, &arg) == 2) {
        if (op == 'd' || op == 'u') {
            emit(EV_KEY, arg, op == 'd');
            emit(EV_SYN, SYN_REPORT, 0);
        } else if (op == 's') {
            usleep(arg * 1000);
        }
    }
    usleep(300000);
    ioctl(fd, UI_DEV_DESTROY);
    close(fd);
    return 0;
}
