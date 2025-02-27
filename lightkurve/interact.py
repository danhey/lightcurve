"""Provides tools for interactive visualizations.

Example use
-----------
The functions in this module are used to create Bokeh-based visualization
widgets.  For example, the following code will create an interactive
visualization widget showing the pixel data and a lightcurve::

    # SN 2018 oh Supernova example
    from lightkurve import KeplerTargetPixelFile
    tpf = KeplerTargetPixelFile.from_archive(228682548)
    tpf.interact()

Note that this will only work inside a Jupyter notebook at this time.
"""
from __future__ import division, print_function
import os
import logging
import warnings

import numpy as np
import astropy.units as u
from astropy.coordinates import SkyCoord, Angle
from astropy.stats import sigma_clip
from astropy.utils.exceptions import AstropyUserWarning

from .utils import KeplerQualityFlags, LightkurveWarning

log = logging.getLogger(__name__)

# Import the optional Bokeh dependency, or print a friendly error otherwise.
try:
    import bokeh  # Import bokeh first so we get an ImportError we can catch
    from bokeh.io import show, output_notebook, push_notebook
    from bokeh.plotting import figure, ColumnDataSource
    from bokeh.models import LogColorMapper, Slider, RangeSlider, \
        Span, ColorBar, LogTicker, Range1d, LinearColorMapper, BasicTicker
    from bokeh.layouts import layout, Spacer
    from bokeh.models.tools import HoverTool
    from bokeh.models.widgets import Button, Div
    from bokeh.models.formatters import PrintfTickFormatter
except ImportError:
    # We will print a nice error message in the `show_interact_widget` function
    pass


def prepare_lightcurve_datasource(lc):
    """Prepare a bokeh ColumnDataSource object for tool tips.

    Parameters
    ----------
    lc : LightCurve object
        The light curve to be shown.

    Returns
    -------
    lc_source : bokeh.plotting.ColumnDataSource
    """
    # Convert time into human readable strings, breaks with NaN time
    # See https://github.com/KeplerGO/lightkurve/issues/116
    if (lc.time == lc.time).all():
        human_time = lc.astropy_time.isot
    else:
        human_time = [' '] * len(lc.flux)

    # Convert binary quality numbers into human readable strings
    qual_strings = []
    for bitmask in lc.quality:
        flag_str_list = KeplerQualityFlags.decode(bitmask)
        if len(flag_str_list) == 0:
            qual_strings.append(' ')
        if len(flag_str_list) == 1:
            qual_strings.append(flag_str_list[0])
        if len(flag_str_list) > 1:
            qual_strings.append("; ".join(flag_str_list))

    lc_source = ColumnDataSource(data=dict(
                                 time=lc.time,
                                 time_iso=human_time,
                                 flux=lc.flux,
                                 cadence=lc.cadenceno,
                                 quality_code=lc.quality,
                                 quality=np.array(qual_strings)))
    return lc_source


def prepare_tpf_datasource(tpf, aperture_mask):
    """Prepare a bokeh DataSource object for selection glyphs

    Parameters
    ----------
    tpf : TargetPixelFile
        TPF to be shown.
    aperture_mask : boolean numpy array
        The Aperture mask applied at the startup of interact

    Returns
    -------
    tpf_source : bokeh.plotting.ColumnDataSource
        Bokeh object to be shown.
    """
    npix = tpf.flux[0, :, :].size
    pixel_index_array = np.arange(0, npix, 1).reshape(tpf.flux[0].shape)
    xx = tpf.column + np.arange(tpf.shape[2])
    yy = tpf.row + np.arange(tpf.shape[1])
    xa, ya = np.meshgrid(xx, yy)
    tpf_source = ColumnDataSource(data=dict(xx=xa.astype(float), yy=ya.astype(float)))
    tpf_source.selected.indices = pixel_index_array[aperture_mask].reshape(-1).tolist()
    return tpf_source


def get_lightcurve_y_limits(lc_source):
    """Compute sensible defaults for the Y axis limits of the lightcurve plot.

    Parameters
    ----------
    lc_source : bokeh.plotting.ColumnDataSource
        The lightcurve being shown.

    Returns
    -------
    ymin, ymax : float, float
        Flux min and max limits.
    """
    with warnings.catch_warnings():  # Ignore warnings due to NaNs
        warnings.simplefilter("ignore", AstropyUserWarning)
        flux = sigma_clip(lc_source.data['flux'], sigma=5, masked=False)
    low, high = np.nanpercentile(flux, (1, 99))
    margin = 0.10 * (high - low)
    return low - margin, high + margin


def make_lightcurve_figure_elements(lc, lc_source, ylim_func=None):
    """Make the lightcurve figure elements.

    Parameters
    ----------
    lc : LightCurve
        Lightcurve to be shown.
    lc_source : bokeh.plotting.ColumnDataSource
        Bokeh object that enables the visualization.

    Returns
    ----------
    fig : `bokeh.plotting.figure` instance
    step_renderer : GlyphRenderer
    vertical_line : Span
    """
    if lc.mission == 'K2':
        title = "Lightcurve for {} (K2 C{})".format(
            lc.label, lc.campaign)
    elif lc.mission == 'Kepler':
        title = "Lightcurve for {} (Kepler Q{})".format(
            lc.label, lc.quarter)
    elif lc.mission == 'TESS':
        title = "Lightcurve for {} (TESS Sec. {})".format(
            lc.label, lc.sector)
    else:
        title = "Lightcurve for target {}".format(lc.label)

    fig = figure(title=title, plot_height=340, plot_width=600,
                 tools="pan,wheel_zoom,box_zoom,tap,reset",
                 toolbar_location="below",
                 border_fill_color="whitesmoke")
    fig.title.offset = -10
    fig.yaxis.axis_label = 'Flux (e/s)'
    fig.xaxis.axis_label = 'Time (days)'
    try:
        if (lc.mission == 'K2') or (lc.mission == 'Kepler'):
            fig.xaxis.axis_label = 'Time - 2454833 (days)'
        elif lc.mission == 'TESS':
            fig.xaxis.axis_label = 'Time - 2457000 (days)'
    except AttributeError:  # no mission keyword available
      pass


    if ylim_func is None:
        ylims = get_lightcurve_y_limits(lc_source)
    else:
        ylims = ylim_func(lc)
    fig.y_range = Range1d(start=ylims[0], end=ylims[1])

    # Add step lines, circles, and hover-over tooltips
    fig.step('time', 'flux', line_width=1, color='gray',
             source=lc_source, nonselection_line_color='gray',
             nonselection_line_alpha=1.0)
    circ = fig.circle('time', 'flux', source=lc_source, fill_alpha=0.3, size=8,
                      line_color=None, selection_color="firebrick",
                      nonselection_fill_alpha=0.0,
                      nonselection_fill_color="grey",
                      nonselection_line_color=None,
                      nonselection_line_alpha=0.0,
                      fill_color=None, hover_fill_color="firebrick",
                      hover_alpha=0.9, hover_line_color="white")
    tooltips = [("Cadence", "@cadence"),
                ("Time ({})".format(lc.time_format.upper()),
                 "@time{0,0.000}"),
                ("Time (ISO)", "@time_iso"),
                ("Flux", "@flux"),
                ("Quality Code", "@quality_code"),
                ("Quality Flag", "@quality")]
    fig.add_tools(HoverTool(tooltips=tooltips, renderers=[circ],
                            mode='mouse', point_policy="snap_to_data"))

    # Vertical line to indicate the cadence
    vertical_line = Span(location=lc.time[0], dimension='height',
                         line_color='firebrick', line_width=4, line_alpha=0.5)
    fig.add_layout(vertical_line)

    return fig, vertical_line


def add_gaia_figure_elements(tpf, fig, magnitude_limit=18):
    """Make the Gaia Figure Elements"""
    # Get the positions of the Gaia sources
    c1 = SkyCoord(tpf.ra, tpf.dec, frame='icrs', unit='deg')
    # Use pixel scale for query size
    pix_scale = 4.0  # arcseconds / pixel for Kepler, default
    if tpf.mission == 'TESS':
        pix_scale = 21.0
    # We are querying with a diameter as the radius, overfilling by 2x.
    from astroquery.vizier import Vizier
    Vizier.ROW_LIMIT = -1
    result = Vizier.query_region(c1, catalog=["I/345/gaia2"],
                                 radius=Angle(np.max(tpf.shape[1:]) * pix_scale, "arcsec"))
    no_targets_found_message = ValueError('Either no sources were found in the query region '
                                          'or Vizier is unavailable')
    too_few_found_message = ValueError('No sources found brighter than {:0.1f}'.format(magnitude_limit))
    if result is None:
        raise no_targets_found_message
    elif len(result) == 0:
        raise too_few_found_message
    result = result["I/345/gaia2"].to_pandas()
    result = result[result.Gmag < magnitude_limit]
    if len(result) == 0:
        raise no_targets_found_message

    # Apply correction for proper motion
    year = ((tpf.astropy_time[0].jd - 2457206.375) * u.day).to(u.year)
    pmra = ((np.nan_to_num(np.asarray(result.pmRA)) * u.milliarcsecond/u.year) * year).to(u.deg).value
    pmdec = ((np.nan_to_num(np.asarray(result.pmDE)) * u.milliarcsecond/u.year) * year).to(u.deg).value
    result.RA_ICRS += pmra
    result.DE_ICRS += pmdec

    # Convert to pixel coordinates
    radecs = np.vstack([result['RA_ICRS'], result['DE_ICRS']]).T
    coords = tpf.wcs.all_world2pix(radecs, 0)

    # Gently size the points by their Gaia magnitude
    sizes = 64.0 / 2**(result['Gmag']/5.0)
    one_over_parallax = 1.0 / (result['Plx']/1000.)
    source = ColumnDataSource(data=dict(ra=result['RA_ICRS'],
                                        dec=result['DE_ICRS'],
                                        pmra=result['pmRA'],
                                        pmde=result['pmDE'],
                                        source=result['Source'].astype(str),
                                        Gmag=result['Gmag'],
                                        plx=result['Plx'],
                                        one_over_plx=one_over_parallax,
                                        x=coords[:, 0] + tpf.column,
                                        y=coords[:, 1] + tpf.row,
                                        size=sizes))

    r = fig.circle('x', 'y', source=source, fill_alpha=0.3, size='size',
                   line_color=None, selection_color="firebrick",
                   nonselection_fill_alpha=0.0, nonselection_line_color=None,
                   nonselection_line_alpha=0.0, fill_color="firebrick",
                   hover_fill_color="firebrick", hover_alpha=0.9,
                   hover_line_color="white")

    fig.add_tools(HoverTool(tooltips=[("Gaia source", "@source"),
                                      ("G", "@Gmag"),
                                      ("Parallax (mas)", "@plx (~@one_over_plx{0,0} pc)"),
                                      ("RA", "@ra{0,0.00000000}"),
                                      ("DEC", "@dec{0,0.00000000}"),
                                      ("pmRA", "@pmra{0,0.000} mas/yr"),
                                      ("pmDE", "@pmde{0,0.000} mas/yr"),
                                      ("x", "@x"),
                                      ("y", "@y")],
                            renderers=[r],
                            mode='mouse',
                            point_policy="snap_to_data"))
    return fig, r


def make_tpf_figure_elements(tpf, tpf_source, pedestal=None, fiducial_frame=None,
                             plot_width=370, plot_height=340, scale='log', vmin=None, vmax=None,
                             cmap='Viridis256', tools='tap,box_select,wheel_zoom,reset'):
    """Returns the lightcurve figure elements.

    Parameters
    ----------
    tpf : TargetPixelFile
        TPF to show.
    tpf_source : bokeh.plotting.ColumnDataSource
        TPF data source.
    pedestal: float
        A scalar value to be added to the TPF flux values, often to avoid
        taking the log of a negative number in colorbars.
        Defaults to `-min(tpf.flux) + 1`
    fiducial_frame: int
        The tpf slice to start with by default, it is assumed the WCS
        is exact for this frame.
    scale: str
        Color scale for tpf figure. Default is 'log'
    vmin: int [optional]
        Minimum color scale for tpf figure
    vmax: int [optional]
        Maximum color scale for tpf figure
    cmap: str
        Colormap to use for tpf plot. Default is 'Viridis256'
    tools: str
        Bokeh tool list
    Returns
    -------
    fig, stretch_slider : bokeh.plotting.figure.Figure, RangeSlider
    """
    if pedestal is None:
        pedestal = -np.nanmin(tpf.flux) + 1
    if scale == 'linear':
        pedestal = 0

    if tpf.mission in ['Kepler', 'K2']:
        title = 'Pixel data (CCD {}.{})'.format(tpf.module, tpf.output)
    elif tpf.mission == 'TESS':
        title = 'Pixel data (Camera {}.{})'.format(tpf.camera, tpf.ccd)
    else:
        title = "Pixel data"

    # We subtract 0.5 from the range below because pixel coordinates refer to
    # the middle of a pixel, e.g. (col, row) = (10.0, 20.0) is a pixel center.
    fig = figure(plot_width=plot_width, plot_height=plot_height,
                 x_range=(tpf.column-0.5, tpf.column+tpf.shape[2]-0.5),
                 y_range=(tpf.row-0.5, tpf.row+tpf.shape[1]-0.5),
                 title=title, tools=tools,
                 toolbar_location="below",
                 border_fill_color="whitesmoke")

    fig.yaxis.axis_label = 'Pixel Row Number'
    fig.xaxis.axis_label = 'Pixel Column Number'


    vlo, lo, hi, vhi = np.nanpercentile(tpf.flux + pedestal, [0.2, 1, 95, 99.8])
    if vmin is not None:
        vlo, lo = vmin, vmin
    if vmax is not None:
        vhi, hi = vmax, vmax

    if scale == 'log':
        vstep = (np.log10(vhi) - np.log10(vlo)) / 300.0  # assumes counts >> 1.0!
    if scale == 'linear':
        vstep = (vhi - vlo) / 300.0  # assumes counts >> 1.0!

    if scale == 'log':
        color_mapper = LogColorMapper(palette=cmap, low=lo, high=hi)
    elif scale == 'linear':
        color_mapper = LinearColorMapper(palette=cmap, low=lo, high=hi)
    else:
        raise ValueError('Please specify either `linear` or `log` scale for color.')

    fig.image([tpf.flux[fiducial_frame, :, :] + pedestal],
              x=tpf.column-0.5, y=tpf.row-0.5,
              dw=tpf.shape[2], dh=tpf.shape[1], dilate=True,
              color_mapper=color_mapper, name="tpfimg")

    # The colorbar will update with the screen stretch slider
    # The colorbar margin increases as the length of the tick labels grows.
    # This colorbar share of the plot window grows, shrinking plot area.
    # This effect is known, some workarounds might work to fix the plot area:
    # https://github.com/bokeh/bokeh/issues/5186

    if scale == 'log':
        ticker = LogTicker(desired_num_ticks=8)
    elif scale == 'linear':
        ticker = BasicTicker(desired_num_ticks=8)

    color_bar = ColorBar(color_mapper=color_mapper,
                         ticker=ticker,
                         label_standoff=-10, border_line_color=None,
                         location=(0, 0), background_fill_color='whitesmoke',
                         major_label_text_align='left',
                         major_label_text_baseline='middle',
                         title='e/s', margin=0)
    fig.add_layout(color_bar, 'right')

    color_bar.formatter = PrintfTickFormatter(format="%14i")

    if tpf_source is not None:
        fig.rect('xx', 'yy', 1, 1, source=tpf_source, fill_color='gray',
                fill_alpha=0.4, line_color='white')

    # Configure the stretch slider and its callback function
    if scale == 'log':
        start, end = np.log10(vlo), np.log10(vhi)
        values = (np.log10(lo), np.log10(hi))
    elif scale == 'linear':
        start, end = vlo, vhi
        values = (lo, hi)

    stretch_slider = RangeSlider(start=start,
                                 end=end,
                                 step=vstep,
                                 title='Screen Stretch ({})'.format(scale),
                                 value=values,
                                 orientation='horizontal',
                                 width=200,
                                 direction='ltr',
                                 show_value=True,
                                 sizing_mode='fixed',
                                 height=15,
                                 name='tpfstretch')

    def stretch_change_callback_log(attr, old, new):
        """TPF stretch slider callback."""
        fig.select('tpfimg')[0].glyph.color_mapper.high = 10**new[1]
        fig.select('tpfimg')[0].glyph.color_mapper.low = 10**new[0]

    def stretch_change_callback_linear(attr, old, new):
        """TPF stretch slider callback."""
        fig.select('tpfimg')[0].glyph.color_mapper.high = new[1]
        fig.select('tpfimg')[0].glyph.color_mapper.low = new[0]

    if scale == 'log':
        stretch_slider.on_change('value', stretch_change_callback_log)
    if scale == 'linear':
        stretch_slider.on_change('value', stretch_change_callback_linear)

    return fig, stretch_slider


def make_default_export_name(tpf, suffix='custom-lc'):
    """makes the default name to save a custom interact mask"""
    fn = tpf.hdu.filename()
    if fn is None:
        outname = "{}_{}_{}.fits".format(tpf.mission, tpf.targetid, suffix)
    else:
        base = os.path.basename(fn)
        outname = base.rsplit('.fits')[0] + '-{}.fits'.format(suffix)
    return outname


def show_interact_widget(tpf, notebook_url='localhost:8888',
                         lc=None,
                         max_cadences=30000,
                         aperture_mask='pipeline',
                         exported_filename=None,
                         transform_func=None,
                         ylim_func=None,
                         vmin=None,
                         vmax=None,
                         scale='log',
                         cmap='Viridis256'):
    """Display an interactive Jupyter Notebook widget to inspect the pixel data.

    The widget will show both the lightcurve and pixel data.  The pixel data
    supports pixel selection via Bokeh tap and box select tools in an
    interactive javascript user interface.

    Note: at this time, this feature only works inside an active Jupyter
    Notebook, and tends to be too slow when more than ~30,000 cadences
    are contained in the TPF (e.g. short cadence data).

    Parameters
    ----------
    tpf : lightkurve.TargetPixelFile
        Target Pixel File to interact with
    notebook_url: str
        Location of the Jupyter notebook page (default: "localhost:8888")
        When showing Bokeh applications, the Bokeh server must be
        explicitly configured to allow connections originating from
        different URLs. This parameter defaults to the standard notebook
        host and port. If you are running on a different location, you
        will need to supply this value for the application to display
        properly. If no protocol is supplied in the URL, e.g. if it is
        of the form "localhost:8888", then "http" will be used.
    max_cadences: int
        Raise a RuntimeError if the number of cadences shown is larger than
        this value. This limit helps keep browsers from becoming unresponsive.
    aperture_mask : array-like, 'pipeline', 'threshold' or 'all'
        A boolean array describing the aperture such that `True` means
        that the pixel will be used.
        If None or 'all' are passed, all pixels will be used.
        If 'pipeline' is passed, the mask suggested by the official pipeline
        will be returned.
        If 'threshold' is passed, all pixels brighter than 3-sigma above
        the median flux will be used.
    exported_filename: str
        An optional filename to assign to exported fits files containing
        the custom aperture mask generated by clicking on pixels in interact.
        The default adds a suffix '-custom-aperture-mask.fits' to the
        TargetPixelFile basename.
    transform_func: function
        A function that transforms the lightcurve.  The function takes in a
        LightCurve object as input and returns a LightCurve object as output.
        The function can be complex, such as detrending the lightcurve.  In this
        way, the interactive selection of aperture mask can be evaluated after
        inspection of the transformed lightcurve.  The transform_func is applied
        before saving a fits file.  Default: None (no transform is applied).
    ylim_func: function
        A function that returns ylimits (low, high) given a LightCurve object.
        The default is to return an expanded window around the 10-90th
        percentile of lightcurve flux values.
    scale: str
        Color scale for tpf figure. Default is 'log'
    vmin: int [optional]
        Minimum color scale for tpf figure
    vmax: int [optional]
        Maximum color scale for tpf figure
    cmap: str
        Colormap to use for tpf plot. Default is 'Viridis256'
    """
    try:
        import bokeh
        if bokeh.__version__[0] == '0':
            warnings.warn("interact() requires Bokeh version 1.0 or later", LightkurveWarning)
    except ImportError:
        log.error("The interact() tool requires the `bokeh` Python package; "
                  "you can install bokeh using e.g. `conda install bokeh`.")
        return None

    aperture_mask = tpf._parse_aperture_mask(aperture_mask)
    if ~aperture_mask.any():
        log.error("No pixels in `aperture_mask`, finding optimum aperture using `tpf.create_threshold_mask`.")
        aperture_mask = tpf.create_threshold_mask()
    if ~aperture_mask.any():
        log.error("No pixels in `aperture_mask`, using all pixels.")
        aperture_mask = tpf._parse_aperture_mask('all')


    if exported_filename is None:
        exported_filename = make_default_export_name(tpf)
    try:
        exported_filename = str(exported_filename)
    except:
        log.error('Invalid input filename type for interact()')
        raise
    if ('.fits' not in exported_filename.lower()):
        exported_filename += '.fits'

    if lc is None:
        lc = tpf.to_lightcurve(aperture_mask=aperture_mask)
        tools = 'tap,box_select,wheel_zoom,reset'
    else:
        lc = lc.copy()
        tools = 'wheel_zoom,reset'
        aperture_mask = np.zeros(tpf.flux.shape[1:]).astype(bool)
        aperture_mask[0, 0] = True

    lc.meta['aperture_mask'] = aperture_mask

    if transform_func is not None:
        lc = transform_func(lc)

    npix = tpf.flux[0, :, :].size
    pixel_index_array = np.arange(0, npix, 1).reshape(tpf.flux[0].shape)

    # Bokeh cannot handle many data points
    # https://github.com/bokeh/bokeh/issues/7490
    if len(lc.cadenceno) > max_cadences:
        msg = 'Interact cannot display more than {} cadences.'
        raise RuntimeError(msg.format(max_cadences))

    def create_interact_ui(doc):
        # The data source includes metadata for hover-over tooltips
        lc_source = prepare_lightcurve_datasource(lc)
        tpf_source = prepare_tpf_datasource(tpf, aperture_mask)

        # Create the lightcurve figure and its vertical marker
        fig_lc, vertical_line = make_lightcurve_figure_elements(lc, lc_source,
                                                            ylim_func=ylim_func)

        # Create the TPF figure and its stretch slider
        pedestal = -np.nanmin(tpf.flux) + 1
        if scale == 'linear':
            pedestal = 0
        fig_tpf, stretch_slider = make_tpf_figure_elements(tpf, tpf_source,
                                                           pedestal=pedestal,
                                                           fiducial_frame=0,
                                                           vmin=vmin, vmax=vmax,
                                                           scale=scale, cmap=cmap,
                                                           tools=tools)

        # Helper lookup table which maps cadence number onto flux array index.
        tpf_index_lookup = {cad: idx for idx, cad in enumerate(tpf.cadenceno)}

        # Interactive slider widgets and buttons to select the cadence number
        cadence_slider = Slider(start=np.min(tpf.cadenceno),
                                end=np.max(tpf.cadenceno),
                                value=np.min(tpf.cadenceno),
                                step=1,
                                title="Cadence Number",
                                width=490)
        r_button = Button(label=">", button_type="default", width=30)
        l_button = Button(label="<", button_type="default", width=30)
        export_button = Button(label="Save Lightcurve",
                               button_type="success", width=120)
        message_on_save = Div(text=' ',width=600, height=15)

        # Callbacks
        def _create_lightcurve_from_pixels(tpf, selected_pixel_indices,
                                            transform_func=transform_func):
            """Create the lightcurve from the selected pixel index list"""
            selected_indices = np.array(selected_pixel_indices)
            selected_mask = np.isin(pixel_index_array, selected_indices)
            lc_new = tpf.to_lightcurve(aperture_mask=selected_mask)
            lc_new.meta['aperture_mask'] = selected_mask
            if transform_func is not None:
                lc_transformed = transform_func(lc_new)
                if (len(lc_transformed) != len(lc_new)):
                    warnings.warn('Dropping cadences in `transform_func` is not '
                                  'yet supported due to fixed time coordinates.'
                            'Skipping the transformation...', LightkurveWarning)
                else:
                    lc_new = lc_transformed
                    lc_new.meta['aperture_mask'] = selected_mask
            return lc_new

        def update_upon_pixel_selection(attr, old, new):
            """Callback to take action when pixels are selected."""
            # Check if a selection was "re-clicked", then de-select
            if ((sorted(old) == sorted(new)) & (new != [])):
                # Trigger recursion
                tpf_source.selected.indices = new[1:]

            if new != []:
                lc_new = _create_lightcurve_from_pixels(tpf, new, transform_func=transform_func)
                lc_source.data['flux'] = lc_new.flux

                if ylim_func is None:
                    ylims = get_lightcurve_y_limits(lc_source)
                else:
                    ylims = ylim_func(lc_new)
                fig_lc.y_range.start = ylims[0]
                fig_lc.y_range.end = ylims[1]
            else:
                lc_source.data['flux'] = lc.flux * 0.0
                fig_lc.y_range.start = -1
                fig_lc.y_range.end = 1

            message_on_save.text = " "
            export_button.button_type = "success"

        def update_upon_cadence_change(attr, old, new):
            """Callback to take action when cadence slider changes"""
            if new in tpf.cadenceno:
                frameno = tpf_index_lookup[new]
                fig_tpf.select('tpfimg')[0].data_source.data['image'] = \
                    [tpf.flux[frameno, :, :] + pedestal]
                vertical_line.update(location=tpf.time[frameno])
            else:
                fig_tpf.select('tpfimg')[0].data_source.data['image'] = \
                    [tpf.flux[0, :, :] * np.NaN]
            lc_source.selected.indices = []

        def go_right_by_one():
            """Step forward in time by a single cadence"""
            existing_value = cadence_slider.value
            if existing_value < np.max(tpf.cadenceno):
                cadence_slider.value = existing_value + 1

        def go_left_by_one():
            """Step back in time by a single cadence"""
            existing_value = cadence_slider.value
            if existing_value > np.min(tpf.cadenceno):
                cadence_slider.value = existing_value - 1

        def save_lightcurve():
            """Save the lightcurve as a fits file with mask as HDU extension"""
            if tpf_source.selected.indices != []:
                lc_new = _create_lightcurve_from_pixels(tpf, tpf_source.selected.indices,
                                                    transform_func=transform_func)
                lc_new.to_fits(exported_filename, overwrite=True,
                               flux_column_name='SAP_FLUX',
                               aperture_mask=lc_new.meta['aperture_mask'].astype(np.int),
                               SOURCE='lightkurve interact',
                               NOTE='custom mask',
                               MASKNPIX=np.nansum(lc_new.meta['aperture_mask']))
                if message_on_save.text == " ":
                    text = '<font color="black"><i>Saved file {} </i></font>'
                    message_on_save.text = text.format(exported_filename)
                    export_button.button_type = "success"
                else:
                    text = '<font color="gray"><i>Saved file {} </i></font>'
                    message_on_save.text = text.format(exported_filename)
            else:
                text = '<font color="gray"><i>No pixels selected, no mask saved</i></font>'
                export_button.button_type = "warning"
                message_on_save.text = text

        def jump_to_lightcurve_position(attr, old, new):
            if new != []:
                cadence_slider.value = lc.cadenceno[new[0]]

        # Map changes to callbacks
        r_button.on_click(go_right_by_one)
        l_button.on_click(go_left_by_one)
        tpf_source.selected.on_change('indices', update_upon_pixel_selection)
        lc_source.selected.on_change('indices', jump_to_lightcurve_position)
        export_button.on_click(save_lightcurve)
        cadence_slider.on_change('value', update_upon_cadence_change)

        # Layout all of the plots
        sp1, sp2, sp3, sp4 = (Spacer(width=15), Spacer(width=30),
                              Spacer(width=80), Spacer(width=60))
        widgets_and_figures = layout([fig_lc, fig_tpf],
                                     [l_button, sp1, r_button, sp2,
                                     cadence_slider, sp3, stretch_slider],
                                     [export_button, sp4, message_on_save])
        doc.add_root(widgets_and_figures)

    output_notebook(verbose=False, hide_banner=True)
    return show(create_interact_ui, notebook_url=notebook_url)


def show_skyview_widget(tpf, notebook_url='localhost:8888', magnitude_limit=18):
    """skyview

    Parameters
    ----------
    tpf : lightkurve.TargetPixelFile
        Target Pixel File to interact with
    notebook_url: str
        Location of the Jupyter notebook page (default: "localhost:8888")
        When showing Bokeh applications, the Bokeh server must be
        explicitly configured to allow connections originating from
        different URLs. This parameter defaults to the standard notebook
        host and port. If you are running on a different location, you
        will need to supply this value for the application to display
        properly. If no protocol is supplied in the URL, e.g. if it is
        of the form "localhost:8888", then "http" will be used.
    magnitude_limit : float
        A value to limit the results in based on Gaia Gmag. Default, 18.
    """
    try:
        import bokeh
        if bokeh.__version__[0] == '0':
            warnings.warn("interact_sky() requires Bokeh version 1.0 or later",
                          LightkurveWarning)
    except ImportError:
        log.error("The interact_sky() tool requires the `bokeh` Python package; "
                  "you can install bokeh using e.g. `conda install bokeh`.")
        return None

    # Try to identify the "fiducial frame", for which the TPF WCS is exact
    zp = (tpf.pos_corr1 == 0) & (tpf.pos_corr2 == 0)
    zp_loc, = np.where(zp)

    if len(zp_loc) == 1:
        fiducial_frame = zp_loc[0]
    else:
        fiducial_frame = 0

    def create_interact_ui(doc):
        # The data source includes metadata for hover-over tooltips
        tpf_source = None

        # Create the TPF figure and its stretch slider
        fig_tpf, stretch_slider = make_tpf_figure_elements(tpf, tpf_source,
                                                fiducial_frame=fiducial_frame,
                                                plot_width=640, plot_height=600)
        fig_tpf, r = add_gaia_figure_elements(tpf, fig_tpf,
                                              magnitude_limit=magnitude_limit)

        # Optionally override the default title
        if tpf.mission == 'K2':
            fig_tpf.title.text = "Skyview for EPIC {}, K2 Campaign {}, CCD {}.{}".format(
                                tpf.targetid, tpf.campaign, tpf.module, tpf.output)
        elif tpf.mission == 'Kepler':
            fig_tpf.title.text = "Skyview for KIC {}, Kepler Quarter {}, CCD {}.{}".format(
                            tpf.targetid, tpf.quarter, tpf.module, tpf.output)
        elif tpf.mission == 'TESS':
            fig_tpf.title.text = 'Skyview for TESS {} Sector {}, Camera {}.{}'.format(
                            tpf.targetid, tpf.sector, tpf.camera, tpf.ccd)

        # Layout all of the plots
        widgets_and_figures = layout([fig_tpf, stretch_slider])
        doc.add_root(widgets_and_figures)

    output_notebook(verbose=False, hide_banner=True)
    return show(create_interact_ui, notebook_url=notebook_url)
