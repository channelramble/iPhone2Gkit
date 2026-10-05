/* Read iBoot-159's console through its legacy USB message protocol.
 * This is read-only apart from the transient printenv command. */
#include <libirecovery.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
extern int irecv_usb_bulk_transfer(irecv_client_t, unsigned char, unsigned char *, int, int *, unsigned int);
struct __attribute__((packed)) message { uint16_t code, magic; uint32_t size, address; };
static int exchange(irecv_client_t client, struct message *m) {
    int count = 0;
    int err = irecv_usb_interrupt_transfer(client, 0x04, (unsigned char *)m, sizeof(*m), &count, 10000);
    if (err || count != sizeof(*m)) return 1;
    err = irecv_usb_interrupt_transfer(client, 0x83, (unsigned char *)m, sizeof(*m), &count, 10000);
    return err || count != sizeof(*m) || m->magic != 0x1234;
}
int main(void) {
    irecv_client_t client = NULL;
    if (irecv_open_with_ecid(&client, 0)) return 1;
    const struct irecv_device_info *info = irecv_get_device_info(client);
    if (!info || info->cpid != 0x8900 || info->ecid) { irecv_close(client); return 1; }
    struct message hello = {0, 0x1234, 0, 0};
    if (exchange(client, &hello)) { irecv_close(client); return 1; }
    /* hello.size is the maximum command length, not a bulk payload. */
    if (irecv_send_command(client, "printenv")) { irecv_close(client); return 1; }
    int result = 1;
    size_t total = 0;
    int empty = 0;
    for (int i = 0; i < 128; i++) {
        struct message m = {0x802, 0x1234, 0, 0};
        if (exchange(client, &m)) { result = 1; break; }
        if ((m.code == 0x809 || m.code == 0x808) && m.size == 0) {
            /* printenv can pause while the previous bulk buffer drains. */
            if (++empty >= 20) { result = 0; break; }
            usleep(20000);
            continue;
        }
        if (m.code != 0x808 || m.size > 65536 - total) break;
        empty = 0;
        unsigned char *data = malloc(m.size);
        if (!data) { result = 1; break; }
        int count = 0;
        int err = irecv_usb_bulk_transfer(client, 0x81, data, m.size, &count, 10000);
        if (!err && count == m.size) { fwrite(data, 1, count, stdout); total += count; }
        free(data);
        if (err || count != m.size) break;
    }
    irecv_close(client);
    return result;
}
