# Installs the R stack of the PK engine into the container image.
# rocker/r-ver pins CRAN to a dated Posit Package Manager snapshot, which serves
# pre-built Linux binaries: versions are frozen by the base-image tag and the
# install takes minutes instead of the hour a source build of nlmixr2 needs.
# pak also installs the system libraries each binary requires.
options(Ncpus = max(1L, parallel::detectCores()))
cat("CRAN snapshot:", getOption("repos")[["CRAN"]], "\n")
install.packages("pak")
pak::pkg_install(c("nlmixr2", "ggplot2", "jsonlite"), ask = FALSE)
suppressPackageStartupMessages(library(nlmixr2))
for (p in c("nlmixr2", "nlmixr2est", "rxode2", "lotri", "ggplot2", "jsonlite")) {
  cat(sprintf("%-11s %s\n", p, as.character(packageVersion(p))))
}
