/// Return the ALSA CPAL host used by the Linux product.
pub fn get_cpal_host() -> cpal::Host {
    cpal::host_from_id(cpal::HostId::Alsa).unwrap_or_else(|_| cpal::default_host())
}
