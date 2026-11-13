# COPR "make srpm" source build: builds the SRPM from the checked-out tree.
# https://docs.pagure.org/copr.copr/user_documentation.html#make-srpm
outdir ?= $(CURDIR)
spec := packaging/shokzer.spec

srpm:
	dnf -y install git rpm-build
	git config --global --add safe.directory "$(CURDIR)"
	version=$$(sed -n 's/^%global pypi_version //p' $(spec)); \
	git archive --format=tar.gz --prefix="shokzer-$$version/" \
		-o "$(outdir)/shokzer-$$version.tar.gz" HEAD
	rpmbuild -bs $(spec) --define "_sourcedir $(outdir)" --define "_srcrpmdir $(outdir)"
