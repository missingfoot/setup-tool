# Maintainer: James <claude@jamessparkes.com>
pkgname=setup-tool
pkgver=0.1.0
pkgrel=1
pkgdesc="Personal app-installer checklist GUI"
arch=('any')
license=('custom:proprietary')
depends=('pyside6' 'python-yaml' 'polkit' 'git' 'base-devel')  # base-devel: makepkg's toolchain
source=('setup-tool' 'setup-tool.desktop' 'apps.yaml' 'LICENSE')
sha256sums=('SKIP' 'SKIP' 'SKIP' 'SKIP')

package() {
    install -Dm755 "$srcdir/setup-tool" "$pkgdir/usr/bin/setup-tool"
    install -d "$pkgdir/usr/lib/setup-tool"
    cp -r "$startdir/setuptoollib" "$pkgdir/usr/lib/setup-tool/setuptoollib"
    find "$pkgdir/usr/lib/setup-tool" -name '__pycache__' -type d -exec rm -rf {} +
    find "$pkgdir/usr/lib/setup-tool" -type f -name '*.py' -exec chmod 644 {} \;
    find "$pkgdir/usr/lib/setup-tool" -type d -exec chmod 755 {} \;

    # apps.yaml + pkgbuilds/ are data, not code: installed under
    # /usr/share so the launcher's base_dir can find them at runtime.
    install -d "$pkgdir/usr/share/setup-tool"
    install -Dm644 "$srcdir/apps.yaml" "$pkgdir/usr/share/setup-tool/apps.yaml"
    cp -r "$startdir/pkgbuilds" "$pkgdir/usr/share/setup-tool/pkgbuilds"
    find "$pkgdir/usr/share/setup-tool/pkgbuilds" -type f -exec chmod 644 {} \;
    find "$pkgdir/usr/share/setup-tool/pkgbuilds" -type d -exec chmod 755 {} \;

    install -Dm644 "$srcdir/setup-tool.desktop" "$pkgdir/usr/share/applications/setup-tool.desktop"
    install -Dm644 "$srcdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
