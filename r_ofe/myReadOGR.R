# Function to read 3d shapefile into SpatialPointsDataFrame
myReadOGR <- function(dir, filename) {
  tmp <- read_sf(dir, filename)
  tmp1 <- sf::as_Spatial(st_zm(tmp))
  tmp2 <- as.data.frame(tmp1)
  head(tmp2)
  coordinates(tmp2) <- ~coords.x1 + coords.x2
  proj4string(tmp2) <- proj4string(tmp1)
  tmp2
}