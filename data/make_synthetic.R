# Synthetic ADPC-like dataset for the PK analysis demonstrator.
#
# 60 subjects, single oral dose of 100 mg, 2-compartment model with
# first-order absorption, simulated with base R only (closed-form solution)
# so that data generation does not depend on the estimation stack.
#
# True values (= the "applicant's reported values"):
#   CL/F 4.2 L/h, V2/F 35 L, Q/F 8.1 L/h, V3/F 120 L, KA 1.1 1/h
#   IIV: 30% CV on CL, 25% on V2, 40% on KA (log-normal)
#   Residual error: combined (proportional 15% + additive 5 ng/mL)
#
# Deliberate anomalies for the QC module:
#   - 3 positive concentration records before the dose
#   - 1 subject reported in ug/mL instead of ng/mL
#
# Usage: Rscript data/make_synthetic.R [output.csv]

SEED <- 20240607L
set.seed(SEED)

args <- commandArgs(trailingOnly = TRUE)
out_file <- if (length(args) >= 1) args[[1]] else file.path("data", "raw", "adpc_synthetic.csv")

N_SUBJ <- 60L
N_RICH <- 20L
DOSE_MG <- 100

theta <- c(CL = 4.2, V2 = 35, Q = 8.1, V3 = 120, KA = 1.1)
cv <- c(CL = 0.30, V2 = 0.25, KA = 0.40)
omega_sd <- sqrt(log(1 + cv^2))
prop_sd <- 0.15
add_sd <- 5 # ng/mL

# Closed-form 2-compartment oral solution (amount in mg, volumes in L -> mg/L)
conc_2cmt_oral <- function(t, dose, cl, v2, q, v3, ka) {
  k10 <- cl / v2
  k12 <- q / v2
  k21 <- q / v3
  s <- k10 + k12 + k21
  alpha <- (s + sqrt(s^2 - 4 * k10 * k21)) / 2
  beta <- (s - sqrt(s^2 - 4 * k10 * k21)) / 2
  a <- ka / v2 * (k21 - alpha) / ((ka - alpha) * (beta - alpha))
  b <- ka / v2 * (k21 - beta) / ((ka - beta) * (alpha - beta))
  c <- -(a + b)
  out <- dose * (a * exp(-alpha * t) + b * exp(-beta * t) + c * exp(-ka * t))
  out[t < 0] <- 0
  out
}

rich_times <- c(0.25, 0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 24, 48, 72)
sparse_windows <- list(c(0.5, 2), c(2, 6), c(8, 24), c(36, 72))
nominal_grid <- c(0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 24, 36, 48, 72)

rows <- list()
for (i in seq_len(N_SUBJ)) {
  id <- sprintf("SYN-%03d", i)
  wt <- round(rnorm(1, 75, 12), 1)
  eta <- rnorm(3, 0, omega_sd)
  p <- list(
    cl = theta[["CL"]] * exp(eta[1]),
    v2 = theta[["V2"]] * exp(eta[2]),
    q = theta[["Q"]],
    v3 = theta[["V3"]],
    ka = theta[["KA"]] * exp(eta[3])
  )
  if (i <= N_RICH) {
    ntime <- rich_times
  } else {
    # one sample per window, nominal time snapped to the protocol grid
    ntime <- vapply(sparse_windows, function(w) {
      g <- nominal_grid[nominal_grid >= w[1] & nominal_grid <= w[2]]
      g[sample.int(length(g), 1)]
    }, numeric(1))
  }
  # actual times deviate from nominal (+/- ~5%, at least a few minutes)
  time <- round(ntime + rnorm(length(ntime), 0, pmax(0.05, 0.05 * ntime)), 3)
  time <- pmax(time, 0.05)
  ipred <- conc_2cmt_oral(time, DOSE_MG, p$cl, p$v2, p$q, p$v3, p$ka) * 1000 # ng/mL
  dv <- ipred * (1 + rnorm(length(ipred), 0, prop_sd)) + rnorm(length(ipred), 0, add_sd)

  rows[[length(rows) + 1]] <- data.frame(
    USUBJID = id, TIME = 0, NTIME = 0, AMT = DOSE_MG, DV = NA_real_,
    EVID = 1L, MDV = 1L, WT = wt
  )
  rows[[length(rows) + 1]] <- data.frame(
    USUBJID = id, TIME = time, NTIME = ntime, AMT = 0, DV = dv,
    EVID = 0L, MDV = 0L, WT = wt
  )
}
d <- do.call(rbind, rows)

# LLOQ: chosen so that roughly 6% of late samples (NTIME >= 24 h) are BLQ
late <- d$EVID == 0 & d$NTIME >= 24
q6 <- stats::quantile(d$DV[late], 0.06, names = FALSE)
LLOQ <- signif(q6, 2)
d$LLOQ <- LLOQ
d$CENS <- 0L
blq <- d$EVID == 0 & d$DV < LLOQ
d$CENS[blq] <- 1L
d$DV[blq] <- NA_real_ # BLQ results are reported without a numeric value
d$DV <- round(d$DV, 2)
d$DVUNIT <- "ng/mL"

# Anomaly 1: three positive concentration records before the dose
pre_ids <- sprintf("SYN-%03d", c(7L, 23L, 41L))
pre <- d[d$EVID == 1 & d$USUBJID %in% pre_ids, ]
pre$TIME <- c(-0.5, -0.25, -1.0)
pre$NTIME <- 0
pre$AMT <- 0
pre$EVID <- 0L
pre$MDV <- 0L
pre$DV <- c(182.4, 96.7, 240.1)
d <- rbind(d, pre)

# Anomaly 2: one subject reported in ug/mL
unit_id <- "SYN-052"
sel <- d$USUBJID == unit_id
d$DV[sel] <- round(d$DV[sel] / 1000, 5)
d$LLOQ[sel] <- d$LLOQ[sel] / 1000
d$DVUNIT[sel] <- "ug/mL"

d <- d[order(d$USUBJID, d$TIME, -d$EVID), ]
d <- d[, c("USUBJID", "TIME", "NTIME", "AMT", "DV", "EVID", "MDV", "CENS", "LLOQ", "WT", "DVUNIT")]

dir.create(dirname(out_file), recursive = TRUE, showWarnings = FALSE)
if (file.exists(out_file)) Sys.chmod(out_file, mode = "0644")
write.csv(d, out_file, row.names = FALSE, na = "")
Sys.chmod(out_file, mode = "0444") # raw data are read-only

obs <- d$EVID == 0 & d$TIME >= 0
late_obs <- obs & d$NTIME >= 24
cat(sprintf("Wrote %s\n", out_file))
cat(sprintf("  subjects: %d, observation records: %d, LLOQ: %g ng/mL\n",
            length(unique(d$USUBJID)), sum(obs), LLOQ))
cat(sprintf("  BLQ: %.1f%% of all samples, %.1f%% of late samples (NTIME >= 24 h)\n",
            100 * mean(d$CENS[obs] == 1), 100 * mean(d$CENS[late_obs] == 1)))
cat(sprintf("  injected anomalies: %d pre-dose concentrations, 1 subject in ug/mL (%s)\n",
            length(pre_ids), unit_id))
