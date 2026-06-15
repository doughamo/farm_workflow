library(raster)
library(gstat)

#' Grid data 
#' 
#' Grid data by kriging and mask using the convex hull of the data
#'
#' @param filename Name of input file.
#'
#' @author Fiona Evans
#' 
#' @param data Data frame containing spatial points.
#' @param Var Name of data column to grid.
#' @param xy Vector of names of data columns containing x and y information.
#' @param r Optional raster defining extnent and size of grid to be created.
#' @param size Size of grid (if r is not provided).
#' @param nsamp Number of data samples used to fit the semivariogram.
#' @param plot Set >0 to plot the fitted semivariogram.
#' 
#' @return Returns a raster.
#' @export
mygrid <- function(data, Var="Yield.tha", xy=c("Longitude", "Latitude"), r=NULL,
                 size=0.0001, nsamp=5000, maxdist=NULL, plot=0) {
  
  if (is.null(maxdist)) maxdist <- size
  
  if (is.null(r)) {
    # Get bounding box
    xmn <- round(min(data[, xy[1]], na.rm=T)/size)*size
    xmx <- round(max(data[, xy[1]], na.rm=T)/size)*size
    ymn <- round(min(data[, xy[2]], na.rm=T)/size)*size
    ymx <- round(max(data[, xy[2]], na.rm=T)/size)*size
    
    # Create empty raster
    r <- raster(xmn=xmn, xmx=xmx, ymn=ymn, ymx=ymx, resolution = size)
  }
  
  # Get points from r
  rdat <- cbind(rasterToPoints(r), raster::as.data.frame(r))
  names(rdat) <- c(xy, "Data")
  gridded(rdat) = eval(coords.fitCommand(xy))
  
  
  # create sample
  samp <- data[sample(nrow(data), nsamp),]
  samp <- samp[!is.na(samp[, Var]), ]
  coordinates(samp) <- samp[, xy]
  
  # estimate semivariogram
  vfc <- vario.fitCommand(Var=Var, data="samp")
  print(vfc)
  vario <- eval(vfc)
  vario.fit = fit.variogram(vario, 
                            model=vgm(model="Sph", psill=max(vario$gamma)*0.9,
                                      range=max(vario$dist)/2, nugget = mean(vario$gamma)/4))
  
  # While singular, try a different sample
  while (sum(vario.fit$range < 0) > 0) {
    samp <- data[sample(nrow(data), 5000),]
    samp <- samp[!is.na(samp[, Var]), ]
    coordinates(samp) <- samp[, xy]
    
    vfc <- vario.fitCommand(Var=Var, data="samp")
    vario <- eval(vfc)
    vario.fit = fit.variogram(vario, 
                              model=vgm(model="Sph", psill=max(vario$gamma)*0.9,
                                        range=max(vario$dist)/2, nugget = mean(vario$gamma)/4))
    
  }
  
  if (plot > 0) plot(vario, vario.fit)
  
  # convert to SpatialPointsDataFrame
  datap <- data
  datap <- datap[!is.na(datap[, Var]), ]
  coordinates(datap) <- datap[, xy]
  
  # krig
  krfc <- krig.fitCommand(Var=Var, datap = "datap", rdat="rdat", model="vario.fit", maxdist=maxdist)
  print(krfc)
  kr <- eval(krfc)
  
  # get convex hull of points
  coords <- data[, xy]
  hull_points <- coords[chull(coords),]
  # convert to polygon
  hull <- hull_points %>%
    Polygon() %>%
    list() %>%
    Polygons(1) %>%
    list() %>%
    SpatialPolygons()
  
  # convert krige object to raster and mask
  mask(raster(kr), hull)
  
}


subst <- function(Command, ...) do.call("substitute", list(Command, list(...)))

coords.fitCommand <- function(xy) {
  fitCommand <- Quote(~Longitude+Latitude)
  fitCommand <- subst(fitCommand, Longitude = as.name(xy[1]))
  fitCommand <- subst(fitCommand, Latitude = as.name(xy[2]))
  fitCommand
}


vario.fitCommand <- function(Var="Yield.tha", data = "data") {
  fitCommand <- Quote(variogram(Var ~ 1, data = d))
  fitCommand <- subst(fitCommand, Var = as.name(Var))
  fitCommand <- subst(fitCommand, d = as.name(data))
  fitCommand
}

krig.fitCommand <- function(Var="Yield.tha", datap = "datap", rdat="rdat", 
                            model="vario.fit", maxdist=0.0001){
  fitCommand <- Quote(krige(Var ~ 1, dp, rdat, model=m, maxdist=d, na.action = na.exclude))
  fitCommand <- subst(fitCommand, Var = as.name(Var))
  fitCommand <- subst(fitCommand, dp = as.name(datap))
  fitCommand <- subst(fitCommand, grd = as.name(rdat))
  fitCommand <- subst(fitCommand, m = as.name(model))
  fitCommand <- subst(fitCommand, d = maxdist)
  fitCommand
}