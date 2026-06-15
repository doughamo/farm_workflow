#
# This is a Shiny web application. You can run the application by clicking
# the 'Run App' button above.
#
# Find out more about building applications with Shiny here:
#
#    http://shiny.rstudio.com/
#

## If a package is installed, it will be loaded. If any 
## are not, the missing package(s) will be installed 
## from CRAN and then loaded.

## First specify the packages of interest
packages = c("tidyverse", "shiny", "Rcpp", "raster", "concaveman", "sf", "sp", 
             "gstat", "RColorBrewer", "fields", "GWmodel", "mapview", "tmap", "htmltools", "leaflet")

## Now load or install&load all
package.check <- lapply(
    packages,
    FUN = function(x) {
        if (!require(x, character.only = TRUE)) {
            install.packages(x, dependencies = TRUE)
            library(x, character.only = TRUE)
        }
    }
)

# Set max file upload size to 10 MB
options(shiny.maxRequestSize = 20*1024^2)

# Define UI for data upload app ----
ui <- fluidPage(
    
    # App title
    titlePanel("OFE Analysis"),
    
    # Sidebar layout with input and output definitions
    sidebarLayout(
        
        # Sidebar panel for inputs
        sidebarPanel(
            
            # Input: Select a yield file
            fileInput("yieldFile", label = "Select yield shapefile .zip", 
                      multiple = FALSE, 
                      accept = c(".zip")),
            
            # Horizontal line
            tags$hr(),
            
            # Input: Select relevant columns
            selectInput("yield", label = "Select yield (wet mass) column", ""),
            selectInput("distance", label = "Select distance column", ""),
            selectInput("swathwidth", label = "Select swath width column", ""),
            
            # Horizontal line
            tags$hr(),
            
            # Input: Select a treatment file
            fileInput("treatmentFile", label = "Select treatment shapefile .zip", 
                      multiple = FALSE, 
                      accept = c(".zip")),
            
            # Horizontal line
            tags$hr(),
            
            # Input: Select product columns
            selectInput("product", label = "Select product column", ""),
            
            # Specify farmer rate
            numericInput("treatmentwidth", label = "Treatment width (m)", value = 75),
            
            # Specify farmer rate
            numericInput("farmerrate", label = "Farmer rate (kg/ha)", value = 50),
            
            # Horizontal line
            tags$hr(),
            
            # Specify costs
            numericInput("installcost", label = "Installation cost ($)", value = 250),
            numericInput("productcost", label = "Fertiliser cost ($/t)", value = 700),
            numericInput("yieldprice", label = "Grain price ($/t)", value = 300),
            
            # Horizontal line
            tags$hr(),
            
            # Make an action button
            actionButton("analyse", label = "Analyse"),
            
            # Horizontal line
            tags$hr(),
            
            # Download map button
            downloadButton("downloadData", label = "Download Map")
            
        ),
        
        # Main panel for displaying outputs
        mainPanel(
            
            # Output: Yield Map
            leafletOutput("yieldMap", height = 800, width = 800)
            
        )
        
    )
)

# Define server logic to read selected file ----
server <- function(input, output, session) {
    
    # Useful functions for plotting
    source('grid.R')
    
    # Wrapper functions for kriging interpolation
    source('myReadOGR.R')
    
    # Colormap
    colp <- two.colors.old(start="brown", end="darkgreen", middle="yellow")
    
    # Projection 
    projen <- "+proj=utm +zone=50 +south +datum=WGS84 +units=m +no_defs"
    
    loadedYieldFile <- reactiveValues()
    
    observeEvent(input$yieldFile, {
        
        # Unzip the yield file to a temporary directory
        shpdf <- input$yieldFile
        unzip(shpdf$datapath, exdir = tempdir())
        
        # read yield data
        loadedYieldFile$yield0 <- as(
            st_read(dsn = tempdir(), layer = sub(".zip$", "", basename(shpdf$name)), quiet = TRUE),
            "Spatial"
        )
                
        # Update inputs for selecting columns
        updateSelectInput(session, inputId = "yield", label = "Select yield (wet mass) column",
                          choices = names(loadedYieldFile$yield0))
        updateSelectInput(session, inputId = "distance", label = "Select distance column",
                          choices = names(loadedYieldFile$yield0))
        updateSelectInput(session, inputId = "swathwidth", label = "Select swath width column",
                          choices = names(loadedYieldFile$yield0))
        
    })
    
    observeEvent(input$treatmentFile, {
        
        # Unzip the treatment file to a temporary directory
        tshpdf <- input$treatmentFile
        unzip(tshpdf$datapath, exdir = tempdir())
        
        # read treatment data
        loadedYieldFile$treatments <- as(
            st_read(dsn = tempdir(), layer = sub(".zip$", "", basename(tshpdf$name)), quiet = TRUE),
            "Spatial"
        )
        
        # Update inputs for selecting columns
        updateSelectInput(session, inputId = "product", label = "Select product column",
                          choices = names(loadedYieldFile$treatments))
        
    })
    
    processedYield <- eventReactive(input$analyse, {
        
        # Read treatment zones for analysis
        treatments <- loadedYieldFile$treatments
        
        # Transform  EN 
        treatments <- spTransform(treatments, projen)
        
        # Rename for convenience
        treatments$product <- treatments@data[,input$product]
        
        # order north-south and add names
        treatments <- treatments[rev(order(coordinates(treatments)[,2])), ]
        treatments$id <- c(1:nrow(treatments))
        
        # Read yield data
        yield0 <- loadedYieldFile$yield0
        
        # Transform  EN 
        yield0 <- spTransform(yield0, projen)
                
        # Rename for convenience
        yield0$yield <- yield0@data[,input$yield]
        yield0$distance <- yield0@data[,input$distance]
        yield0$swathwidth <- yield0@data[,input$swathwidth]
                
        ###
        ### Clean Yield data ----
        ###
                
        # Methods adapted from:
        # 
        # Sudduth, K. A., et al. (2012). Yield Editor 2.0: Software for automated removal of yield map errors. ASABE Annual International Meeting, Dallas, USA.
        # Lee, D. H., et al. (2012). Automated yield map delay identification using phase correlation methodology. Transactions of the ASABE 55(3): 743:752.
                
        # Create flag for data cleaning
        yield0$cleaning <- rep(NA, nrow(yield0))
                
        # Remove geometry duplicates
        indx <- duplicated(coordinates(yield0))
        yield0$cleaning[indx] <- "geom"
                
        # Remove points near paddock boundary, by calculating the concave hull 
        hull <- SpatialPoints(concaveman(coordinates(yield0), concavity=2))
        proj4string(hull) <- proj4string(yield0)
        hullpts <- as.data.frame(hull)
        hullpts <- hullpts[c(1:nrow(hullpts)) %% 5 == 0, ] # Remove every fifth point 
        rad <- 10 # Flag all points within 10 metres
        indx <- rep(FALSE, nrow(yield0))
        for (i in 1:nrow(hullpts)) { # Still slow - should write in C/C++ and put into a package
            x <- hullpts[i, 1]
            y <- hullpts[i, 2]
            this <- coordinates(yield0)[,1] >= x-rad &  coordinates(yield0)[,1] <= x+rad &
            coordinates(yield0)[,2] >= y-rad &  coordinates(yield0)[,2] <= y+rad 
            indx[this] <- TRUE
            }
        yield0$cleaning[indx] <- "edge"
                
        # Remove harvest overlaps ('small' swath widths).
        indx <- yield0$swathwidth <= max(yield0$swathwidth) * 0.707
        yield0$cleaning[indx] <- "swath"
            
        # Remove time lags (distance less than 1st percentile and greater than 99th percentile)
        indx <- yield0$distance <= quantile(yield0$distance, 0.01) | 
            yield0$distance >= quantile(yield0$distance, 0.99)
        yield0$cleaning[indx] <- "time"
                
        # Remove yield outliers
        indx <- yield0$yield <= quantile(yield0$yield, 0.01) | yield0$yield >= quantile(yield0$yield, 0.99)
        yield0$cleaning[indx] <- "outliers"
                
        # Save RAW with cleaning FLAGS to yield0 and subset cleaned data to yield
        yield <- subset(yield0, is.na(cleaning))
        
        rm(yield0)
        
        # Create an empty 10m raster to cover the trial area
        e <- extent(yield)
                
        roundDown <- function(x) floor(min(x)/10) * 10
        roundUp   <- function(x) ceiling(max(x)/10) * 10
        r <- raster(xmn = roundDown(e@xmin), xmx = roundUp(e@xmax), 
                    ymn = roundDown(e@ymin), ymx = roundUp(e@ymax), 
                    resolution = 10)
                
        # Add CRS
        crs(r) <- projen
        
        # Interpolate yield by kriging
        yield$E <- coordinates(yield)[,1]
        yield$N <- coordinates(yield)[,2]
        yield.raster <- mygrid(yield@data, Var="yield", xy=c("E", "N"), r=r,
                               size=10, nsamp=5000, maxdist=25, plot=0)
                
        crs(yield.raster) <- crs(yield)
        
        ###
        ### Create raster maps for fertiliser ----
        ###
        
        # Get treatment for each yield point and subset out trial plots
        (st <- sort(unique(treatments$id)))
        
        yield$rateFert  <- rep(NA, nrow(yield))
        
        for (i in 1:length(st)){
            this <- treatments[treatments$id == st[i], ]
            cc <- complete.cases(over(yield, this))
            yield$rateFert[cc] <- as.numeric(as.character(treatments[treatments$id == st[i], ]$product[1]))
            yield$plot[cc] <- st[i]
        }
        
        # Subset to area with treatment
        yield <- yield[!is.na(yield@data$rateFert),]
        
        # Rasterise fertiliser
        getmode <- function(v, na.rm, ...) {
            v <- v[!is.na(v)]
            uniqv <- unique(v)
            uniqv[which.max(tabulate(match(v, uniqv)))]
        }
        
        
        Fertiliser.raster <- rasterize(yield, r, field = "rateFert", fun=getmode)
        
        # Since the polygons are not exact, there are missing values that need to be filled.
        Fertiliser.raster <- focal(Fertiliser.raster, w=matrix(rep(1,9), nc=3, nr=3), fun=getmode)
        
        # Add CRS
        crs(Fertiliser.raster) <- projen
        
        ###
        ### Calculate gross margin ----
        ###
        
        installation.cost = input$installcost
        product.cost = input$productcost
        yield.price = input$yieldprice
        
        # Apply the farmer rate to area of paddock outside treatments
        Fertiliser.paddock <- Fertiliser.raster
        Fertiliser.paddock[is.na(Fertiliser.paddock[])] <- input$farmerrate
        
        gm.raster = (yield.raster * yield.price) - (Fertiliser.paddock * product.cost / 1000) - installation.cost
        
        ###
        ### GWR analysis ----
        ###   
        
        # Make the bandwidth 1.5 times treatment width
        treatment.width = input$treatmentwidth
        BW <- treatment.width * 1.5
        
        rdat <- as.data.frame(r, xy=T)
        coordinates(rdat) <- ~x + y
        DMr <- gw.dist(dp.locat=coordinates(yield), rp.locat=coordinates(rdat)) 
        GWRr <- gwr.basic(yield ~ rateFert, data = yield, 
                          regression.points = rdat,
                          bw = BW, 
                          dMat = DMr,  
                          kernel = 'gaussian')   
        
        rm(DMr)
        
        # Convert to raster
        dat.all.sp <- rdat
        dat.all.sp$Intercept  <- GWRr$SDF$Intercept
        dat.all.sp$Response <- GWRr$SDF$rateFert * 1000
        
        rm(GWRr)
        
        z <- dat.all.sp[, "Intercept"]
        gridded(z) <- T
        gwr.intercept.raster <- raster(z)
        gwr.intercept.raster <- mask(gwr.intercept.raster, Fertiliser.raster)
        
        z <- dat.all.sp[, "Response"]
        gridded(z) <- T
        gwr.response.raster <- raster(z)
        gwr.response.raster <- mask(gwr.response.raster, Fertiliser.raster)
        
        rm(z, dat.all.sp)
        
        crs(gwr.intercept.raster) <- projen
        crs(gwr.response.raster) <- projen

        ###
        ### Make maps ----
        ###
        
        colr <- two.colors(start="red", middle="yellow", end="darkgreen")
        
        map <- tm_basemap(server = "http://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}") + 
                    tm_shape(treatments, name = "Treatments") +
                        tm_text("product", 
                                style = "cat",
                                legend.hist = F) +
                        tm_borders() +
                    tm_shape(yield.raster, name = "Yield Map") + 
                        tm_raster(palette = colr,
                                  style="cont",
                                  legend.hist = F, 
                                  title = "Yield (t/ha)") +
                    tm_shape(gm.raster, name = "Gross Margin") + 
                        tm_raster(palette = colr,
                                  style="cont",
                                  legend.hist = F,
                                  midpoint = 0,
                                  title = "Gross margin ($/ha)") +
                    tm_shape(gwr.intercept.raster, name = "Intercept") + 
                        tm_raster(palette = colr,
                                  style="cont",
                                  legend.hist = F, 
                                  title = "Intercept (t/ha)") +
                    tm_shape(gwr.response.raster, name = "Response") + 
                        tm_raster(palette = colr,
                                  style="cont",
                                  legend.hist = F, 
                                  midpoint = 0,
                                  title = "Response (kg/kg)")
                
        lf2 <- tmap_leaflet(map)
        
        list(lf2 = lf2)
        
        })
        
    output$yieldMap <- renderLeaflet({
        
        processedYield()$lf2
        
    })
    
    output$downloadData <- downloadHandler(
        filename = function() {
            paste0(sub(".zip$", "", basename(input$yieldFile$name)), ".html")
    },
        content = function(file) {
            htmlwidgets::saveWidget(processedYield()$lf2, file)
    })
}

# Create Shiny app ----
shinyApp(ui, server)