# Koma for the Ricoh GR DIGITAL: the `koma` command. Published as overscanindustries/homebrew-koma (Formula/koma.rb):
#   brew install overscanindustries/koma/koma
# Source: the koma-gr sdist on PyPI, built by packaging/koma_build.py in github.com/overscanindustries/koma.
# Before publishing: upload the sdist, then set its url and sha256 below (the sha256 of dist/koma_gr-1.0.0.tar.gz).
class Koma < Formula
  include Language::Python::Virtualenv

  desc "Looks, tethered shooting and webcam for the 2005 Ricoh GR DIGITAL"
  homepage "https://github.com/overscanindustries/koma"
  url "https://files.pythonhosted.org/packages/source/k/koma-gr/koma_gr-1.0.0.tar.gz"
  sha256 "SDIST_SHA256"
  license "MIT"

  depends_on "libusb"
  depends_on :macos
  depends_on "python@3.13"

  resource "pyusb" do
    url "https://files.pythonhosted.org/packages/00/6b/ce3727395e52b7b76dfcf0c665e37d223b680b9becc60710d4bc08b7b7cb/pyusb-1.3.1.tar.gz"
    sha256 "3af070b607467c1c164f49d5b0caabe8ac78dbed9298d703a8dbf9df4052d17e"
  end

  # koma --tray: the menu bar icon
  resource "pyobjc-core" do
    url "https://files.pythonhosted.org/packages/a5/78/abc4ce5920305780aeb36b4067a86253378b36e29ba96673a3deb02eb03a/pyobjc_core-12.2.2.tar.gz"
    sha256 "3906452339cd06a3bb07df103c2511d4cb0f7a22d8771c0b802eba15d9a642b6"
  end

  resource "pyobjc-framework-cocoa" do
    url "https://files.pythonhosted.org/packages/75/76/49c6da2c6a831020b4854ba20079d5a1030474bffc776b7b73c2eeff8c15/pyobjc_framework_cocoa-12.2.2.tar.gz"
    sha256 "c96c0ef69a71afbbb0e6a7d594b455c5fe47d62e0db376ee7a2b4b828c16ace9"
  end

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      Run `koma` and the Koma app opens in the browser at http://localhost:8765/.
      `koma --tray` keeps it in the menu bar instead. `koma doctor` checks USB.
      Set the camera's USB CONNECTION to ORIGINAL, turn it on, then plug in the cable.
    EOS
  end

  # No USB: brew test runs without a camera. `koma --version` prints "Koma 1.0" for 1.0.0 (komalauncher.version_text
  # drops a .0 patch digit), so match on major.minor; "Koma 1.0.1" still contains "Koma 1.0".
  test do
    assert_match "Koma #{version.major_minor}", shell_output("#{bin}/koma --version")
  end
end
