# Fedora COPR builds DiscBay from this repository with the "make srpm" method.
# COPR runs:  make -f .copr/Makefile srpm outdir=<dir> spec=<spec>

srpm:
	dnf -y install git rpm-build
	git config --global --add safe.directory "$(CURDIR)"
	OUTDIR=$(outdir) bash packaging/build-rpm.sh --srpm
