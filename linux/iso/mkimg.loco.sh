# shellcheck shell=sh disable=SC2034
# Profil mkimage officiel Alpine : BIOS/UEFI, runtime disponible hors ligne.
profile_loco() {
    profile_standard
    image_name="loco"
    title="loco appliance"
    desc="Serveur de dépôts et de caches loco (Alpine, BusyBox)."
    hostname="loco"
    kernel_flavors="lts"
    # tty0 suit la console active : son getty vole sinon le clavier de l'assistant (tty7).
    kernel_cmdline="$kernel_cmdline console=ttyS0,115200n8 console=tty1"
    # Le chargeur BIOS et EFI sont disponibles aussi hors ligne dans setup-disk.
    apks="$apks grub-bios grub-efi syslinux linux-lts linux-firmware linux-firmware-none"
    apks="$apks $(cat "$LOCO_ISO_DIR/packages")"
    apkovl="genapkovl-loco.sh"
}
