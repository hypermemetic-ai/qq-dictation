use clap::Parser;
use handy_app_lib::CliArgs;

fn main() {
    let cli_args = CliArgs::parse();

    // DMABUF rendering is unstable on several Linux GPU/display combinations.
    std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");

    handy_app_lib::run(cli_args)
}
