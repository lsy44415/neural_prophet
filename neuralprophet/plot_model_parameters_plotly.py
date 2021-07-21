import numpy as np
import pandas as pd
import logging
import datetime
import time
import torch
from neuralprophet import time_dataset
from neuralprophet.utils import set_y_as_percent
# from neuralprophet.plot_model_parameters import predict_season_from_dates, predict_one_season


log = logging.getLogger("NP.plotly")

try:
    import plotly.graph_objs as go
    from plotly.subplots import make_subplots
except ImportError:
    log.error("Importing plotly failed. Interactive plots will not work.")


def get_parameter_components(m, forecast_in_focus,quantile=None):
    """Provides the components for plotting parameters.

    Args:
        m (NeuralProphet): fitted model.

    Returns:
        A list of dicts consisting the parameter plot components.
    """
    quantile_index = m.model.quantiles.index(quantile)
    # Identify components to be plotted
    # as dict: {plot_name, }
    components = [{"plot_name": "Trend"}]
    if m.config_trend.n_changepoints > 0:
        components.append({"plot_name": "Trend Rate Change"})

    # Plot  seasonalities, if present
    if m.season_config is not None:
        for name in m.season_config.periods:
            components.append({"plot_name": "seasonality", "comp_name": name})

    if m.n_lags > 0:
        components.append(
            {
                "plot_name": "lagged weights",
                "comp_name": "AR",
                "weights": m.model.ar_weights.detach().numpy(),
                "focus": forecast_in_focus,
            }
        )

    # all scalar regressors will be plotted together
    # collected as tuples (name, weights)

    # Add Regressors
    additive_future_regressors = []
    multiplicative_future_regressors = []
    if m.regressors_config is not None:
        for regressor, configs in m.regressors_config.items():
            mode = configs["mode"]
            regressor_param = m.model.get_reg_weights(regressor)[quantile_index, :]
            if mode == "additive":
                additive_future_regressors.append((regressor, regressor_param.detach().numpy()))
            else:
                multiplicative_future_regressors.append((regressor, regressor_param.detach().numpy()))

    additive_events = []
    multiplicative_events = []
    # Add Events
    # add the country holidays
    if m.country_holidays_config is not None:
        for country_holiday in m.country_holidays_config["holiday_names"]:
            event_params = m.model.get_event_weights(country_holiday)
            weight_list = [(key, param.detach().numpy()[quantile_index, :]) for key, param in event_params.items()]
            mode = m.country_holidays_config["mode"]
            if mode == "additive":
                additive_events = additive_events + weight_list
            else:
                multiplicative_events = multiplicative_events + weight_list

    # add the user specified events
    if m.events_config is not None:
        for event, configs in m.events_config.items():
            event_params = m.model.get_event_weights(event)
            weight_list = [(key, param.detach().numpy()[quantile_index, :]) for key, param in event_params.items()]
            mode = configs["mode"]
            if mode == "additive":
                additive_events = additive_events + weight_list
            else:
                multiplicative_events = multiplicative_events + weight_list

    # Add Covariates
    lagged_scalar_regressors = []
    if m.config_covar is not None:
        for name in m.config_covar.keys():
            if m.config_covar[name].as_scalar:
                lagged_scalar_regressors.append((name, m.model.get_covar_weights(name).detach().numpy()))
            else:
                components.append(
                    {
                        "plot_name": "lagged weights",
                        "comp_name": 'Lagged Regressor "{}"'.format(name),
                        "weights": m.model.get_covar_weights(name).detach().numpy(),
                        "focus": forecast_in_focus,
                    }
                )

    if len(additive_future_regressors) > 0:
        components.append({"plot_name": "Additive future regressor"})
    if len(multiplicative_future_regressors) > 0:
        components.append({"plot_name": "Multiplicative future regressor"})
    if len(lagged_scalar_regressors) > 0:
        components.append({"plot_name": "Lagged scalar regressor"})
    if len(additive_events) > 0:
        additive_events = [(key, weight * m.data_params["y"].scale) for (key, weight) in additive_events]

        components.append({"plot_name": "Additive event"})
    if len(multiplicative_events) > 0:
        components.append({"plot_name": "Multiplicative event"})

    output_dict = {
        "components": components,
        "additive_future_regressors": additive_future_regressors,
        "additive_events": additive_events,
        "multiplicative_future_regressors": multiplicative_future_regressors,
        "multiplicative_events": multiplicative_events,
    }

    return output_dict


def predict_one_season(m, name, n_steps=100, quantile=None):
    config = m.season_config.periods[name]
    t_i = np.arange(n_steps + 1) / float(n_steps)
    features = time_dataset.fourier_series_t(
        t=t_i * config.period, period=config.period, series_order=config.resolution
    )
    features = torch.from_numpy(np.expand_dims(features, 1))
    quantile_index = m.model.quantiles.index(quantile)
    predicted = m.model.seasonality(features=features, name=name)[:, quantile_index, :]
    predicted = predicted.squeeze().detach().numpy()
    if m.season_config.mode == "additive":
        predicted = predicted * m.data_params["y"].scale
    return t_i, predicted


def predict_season_from_dates(m, dates, name, quantile=None):
    config = m.season_config.periods[name]
    features = time_dataset.fourier_series(dates=dates, period=config.period, series_order=config.resolution)
    features = torch.from_numpy(np.expand_dims(features, 1))
    quantile_index = m.model.quantiles.index(quantile)
    predicted = m.model.seasonality(features=features, name=name)[:, quantile_index, :]
    predicted = predicted.squeeze().detach().numpy()
    if m.season_config.mode == "additive":
        predicted = predicted * m.data_params["y"].scale
    return predicted


def plot_trend_change(m, quantile=None,plot_name="Trend Change"):
    """Make a barplot of the magnitudes of trend-changes.

    Args:
        quantile (float): the quantile for which the trend changes are plotted
        m (NeuralProphet): fitted model.
        plot_name (str): Name of the plot Title.

    Returns:
        A dictionary with Plotly traces, xaxis and yaxis
    """
    zeroline_color = "#AAA"
    color = "#0072B2"

    start = m.data_params["ds"].shift
    scale = m.data_params["ds"].scale
    time_span_seconds = scale.total_seconds()
    cp_t = []
    for cp in m.model.config_trend.changepoints:
        cp_t.append(start + datetime.timedelta(seconds=cp * time_span_seconds))
    quantile_index = m.model.quantiles.index(quantile)
    weights = m.model.get_trend_deltas().detach().numpy()[quantile_index, :].squeeze()

    # add end-point to force scale to match trend plot
    cp_t.append(start + scale)
    weights = np.append(weights, [0.0])
    width = time_span_seconds / 175000 / m.config_trend.n_changepoints

    text = None

    traces = []
    traces.append(
        go.Bar(
            name=plot_name,
            x=cp_t,
            y=weights,
            marker_color=color,
        )
    )
    xaxis = go.layout.XAxis(type="date")
    yaxis = go.layout.YAxis(
        rangemode="normal",
        title=go.layout.yaxis.Title(text=plot_name),
        zerolinecolor=zeroline_color,
    )

    return {"traces": traces, "xaxis": xaxis, "yaxis": yaxis}


def plot_trend(m, quantile=None, plot_name="Trend Change"):
    """Make a barplot of the magnitudes of trend-changes.

    Args:
        m (NeuralProphet): fitted model.
        plot_name (str): Name of the plot Title.

    Returns:
        A dictionary with Plotly traces, xaxis and yaxis
    """

    traces = []
    color = "#0072B2"
    zeroline_color = "#AAA"
    line_width = 1

    t_start = m.data_params["ds"].shift
    t_end = t_start + m.data_params["ds"].scale
    quantile_index = m.model.quantiles.index(quantile)
    if m.config_trend.n_changepoints == 0:
        fcst_t = pd.Series([t_start, t_end]).dt.to_pydatetime()
        trend_0 = m.model.bias[quantile_index, :].detach().numpy().squeeze()
        if m.config_trend.growth == "off":
            trend_1 = trend_0
        else:
            trend_1 = trend_0 + m.model.trend_k0[quantile_index, :].detach().numpy()
        trend_0 = trend_0 * m.data_params["y"].scale + m.data_params["y"].shift
        trend_1 = trend_1 * m.data_params["y"].scale + m.data_params["y"].shift
        traces.append(
            go.Scatter(
                name=plot_name,
                x=fcst_t,
                y=trend_0,
                mode="lines",
                line=dict(color=color, width=line_width),
                fill="none",
            )
        )

        traces.append(
            go.Scatter(
                name=plot_name,
                x=fcst_t,
                y=trend_1,
                mode="lines",
                line=dict(color=color, width=line_width),
                fill="none",
            )
        )
    else:
        days = pd.date_range(start=t_start, end=t_end, freq=m.data_freq)
        df_y = pd.DataFrame({"ds": days})
        df_trend = m.predict_trend(df_y, quantile=quantile)
        traces.append(
            go.Scatter(
                name=plot_name,
                x=df_y["ds"].dt.to_pydatetime(),
                y=df_trend["trend"],
                mode="lines",
                line=dict(color=color, width=line_width),
                fill="none",
            )
        )

    xaxis = go.layout.XAxis(type="date")
    yaxis = go.layout.YAxis(
        rangemode="normal",
        title=go.layout.yaxis.Title(text=plot_name),
        zerolinecolor=zeroline_color,
    )

    return {"traces": traces, "xaxis": xaxis, "yaxis": yaxis}


def plot_scalar_weights(weights, plot_name, focus=None, multiplicative=False):
    """Make a barplot of the regressor weights.

    Args:
        weights (list): tuples (name, weights)
        plot_name (string): name of the plot
        focus (int): if provided, show weights for this forecast
            None (default) plot average
        multiplicative (bool): set y axis as percentage=
    Returns:
        A dictionary with Plotly traces, xaxis and yaxis
    """

    traces = []
    zeroline_color = "#AAA"
    color = "#0072B2"

    names = []
    values = []
    for name, weights in weights:
        names.append(name)
        weight = np.squeeze(weights)
        if len(weight.shape) > 1:
            raise ValueError("Not scalar " + plot_name)
        if len(weight.shape) == 1 and len(weight) > 1:
            if focus is not None:
                weight = weight[focus - 1]
            else:
                weight = np.mean(weight)
        values.append(weight)

    traces.append(
        go.Bar(
            name=plot_name,
            x=names,
            y=values,
            marker_color=color,
            width=0.8,
        )
    )

    xaxis = go.layout.XAxis(title=f"{plot_name} name")

    # if len("_".join(names)) > 100:
    #     for tick in xticks:
    #         tick.set_ha("right")
    #         tick.set_rotation(20)
    if "lagged" in plot_name.lower():
        if focus is None:
            yaxis = go.layout.YAxis(
                rangemode="normal",
                title=go.layout.yaxis.Title(text=f"{plot_name} weight (avg)"),
                zerolinecolor=zeroline_color,
            )
        else:
            yaxis = go.layout.YAxis(
                rangemode="normal",
                title=go.layout.yaxis.Title(text=f"{plot_name} weight ({focus})-ahead"),
                zerolinecolor=zeroline_color,
            )
    else:
        yaxis = go.layout.YAxis(
            rangemode="normal",
            title=go.layout.yaxis.Title(text=f"{plot_name} weight"),
            zerolinecolor=zeroline_color,
        )

    if multiplicative:
        yaxis.update(tickformat="%", hoverformat=".2%")

    return {"traces": traces, "xaxis": xaxis, "yaxis": yaxis}


def plot_lagged_weights(weights, comp_name, focus=None):
    """Make a barplot of the importance of lagged inputs.

    Args:
        weights (np.array): model weights as matrix or vector
        comp_name (str): name of lagged inputs
        focus (int): if provided, show weights for this forecast
            None (default) sum over all forecasts and plot as relative percentage
    Returns:
        A dictionary with Plotly traces, xaxis and yaxis
    """
    traces = []
    zeroline_color = "#AAA"
    color = "#0072B2"

    n_lags = weights.shape[1]
    lags_range = list(range(1, 1 + n_lags))[::-1]
    if focus is None:
        weights = np.sum(np.abs(weights), axis=0)
        weights = weights / np.sum(weights)

        traces.append(
            go.Bar(
                name=comp_name,
                x=lags_range,
                y=weights,
                marker_color=color,
            )
        )

    else:
        if len(weights.shape) == 2:
           
            weights = weights[focus - 1, :]

        traces.append(go.Bar(name=comp_name, x=lags_range, y=weights, marker_color=color, width=0.8))

    xaxis = go.layout.XAxis(title=f"{comp_name} lag number")

    if focus is None:
        # ax.set_ylabel("{} relevance".format(comp_name))
        yaxis = go.layout.YAxis(
            rangemode="normal",
            title=go.layout.yaxis.Title(text=f"{comp_name} relevance"),
            zerolinecolor=zeroline_color,
            tickformat=",.0%",
        )
        # ax = set_y_as_percent(ax)
    else:
        yaxis = go.layout.YAxis(
            rangemode="normal",
            title=go.layout.yaxis.Title(text=f"{comp_name} weight ({focus})-ahead"),
            zerolinecolor=zeroline_color,
        )

    return {"traces": traces, "xaxis": xaxis, "yaxis": yaxis}


def plot_yearly(m, quantile=None, comp_name="yearly", yearly_start=0, quick=True, multiplicative=False):
    """Plot the yearly component of the forecast.

    Args:
        m (NeuralProphet): fitted model.
        comp_name (str): Name of seasonality component if previously changed from default 'weekly'.
        yearly_start (int): specifying the start day of the yearly seasonality plot.
            0 (default) starts the year on Jan 1.
            1 shifts by 1 day to Jan 2, and so on.
        quick (bool): use quick low-evel call of model. might break in future.
        multiplicative (bool): set y axis as percentage
    Returns:
        A dictionary with Plotly traces, xaxis and yaxis
    """
    traces = []
    color = "#0072B2"
    line_width = 1
    zeroline_color = "#AAA"

    # Compute yearly seasonality for a Jan 1 - Dec 31 sequence of dates.
    days = pd.date_range(start="2017-01-01", periods=365) + pd.Timedelta(days=yearly_start)
    df_y = pd.DataFrame({"ds": days})
    if quick:
        predicted = predict_season_from_dates(m, dates=df_y["ds"], name=comp_name,quantile=quantile)
    else:
        predicted = m.predict_seasonal_components(df_y, quantile=quantile)[comp_name]
    
    traces.append(
        go.Scatter(
            name=comp_name,
            x=df_y["ds"].dt.to_pydatetime(),
            y=predicted,
            mode="lines",
            line=dict(color=color, width=line_width),
            fill="none",
        )   
    )
    
    xaxis = go.layout.XAxis(title="Day of year",tickformat = "%B %e")
    yaxis = go.layout.YAxis(
        rangemode="normal",
        title=go.layout.yaxis.Title(text=f"Seasonality: {comp_name}"),
        zerolinecolor=zeroline_color,
    )

    if multiplicative:
        yaxis.update(tickformat="%", hoverformat=".2%")

    return {"traces": traces, "xaxis": xaxis, "yaxis": yaxis}


def plot_weekly(m,  quantile=None,comp_name="weekly", weekly_start=0, quick=True, multiplicative=False):
    """Plot the weekly component of the forecast.

    Args:
        m (NeuralProphet): fitted model.
        comp_name (str): Name of seasonality component if previously changed from default 'weekly'.

        weekly_start (int): specifying the start day of the weekly seasonality plot.
            0 (default) starts the week on Sunday.
            1 shifts by 1 day to Monday, and so on.
        quick (bool): use quick low-evel call of model. might break in future.
        multiplicative (bool): set y axis as percentage

    Returns:
        A dictionary with Plotly traces, xaxis and yaxis
    """
    traces = []
    color = "#0072B2"
    line_width = 1
    zeroline_color = "#AAA"

    # Compute weekly seasonality for a Sun-Sat sequence of dates.
    days_i = pd.date_range(start="2017-01-01", periods=7 * 24, freq="H") + pd.Timedelta(days=weekly_start)
    df_w = pd.DataFrame({"ds": days_i})
    if quick:
        predicted = predict_season_from_dates(m, dates=df_w["ds"], name=comp_name, quantile=quantile)
    else:
        predicted = m.predict_seasonal_components(df_w, quantile=quantile)[comp_name]
    days = pd.date_range(start="2017-01-01", periods=7) + pd.Timedelta(days=weekly_start)
    days = days.day_name()

    traces.append(
        go.Scatter(
            name=comp_name,
            x=list(range(len(days_i))), y=predicted, mode="lines", line=dict(color=color, width=line_width), fill="none"),
        
    )

    range_margin = (df_w["ds"].max() - df_w["ds"].min()) * 0.05
    xaxis = go.layout.XAxis(title="Day of week",ticktext=days,tickmode = 'array',tickvals = [11, 35, 59, 83, 107, 131,155])
    yaxis = go.layout.YAxis(
        rangemode="normal",
        title=go.layout.yaxis.Title(text=f"Seasonality: {comp_name}"),
        zerolinecolor=zeroline_color,
    )

    if multiplicative:
        yaxis.update(tickformat="%", hoverformat=".2%")

    return {"traces": traces, "xaxis": xaxis, "yaxis": yaxis}


def plot_daily(m, quantile=None,comp_name="daily", quick=True, multiplicative=False):
    """Plot the daily component of the forecast.

    Args:
        m (NeuralProphet): fitted model.
        comp_name (str): Name of seasonality component if previously changed from default 'daily'.
        weekly_start (int): specifying the start day of the weekly seasonality plot.
            0 (default) starts the week on Sunday.
            1 shifts by 1 day to Monday, and so on.
        quick (bool): use quick low-evel call of model. might break in future.
        multiplicative (bool): set y axis as percentage

    Returns:
        A dictionary with Plotly traces, xaxis and yaxis
    """
    traces = []
    color = "#0072B2"
    line_width = 1
    zeroline_color = "#AAA"

    # Compute daily seasonality
    dates = pd.date_range(start="2017-01-01", periods=24 * 12, freq="5min")
    df = pd.DataFrame({"ds": dates})
    if quick:
        predicted = predict_season_from_dates(m, dates=df["ds"], name=comp_name,quantile=quantile)
    else:
        predicted = m.predict_seasonal_components(df, quantile=quantile)[comp_name]

    traces.append(
        go.Scatter(
            name=comp_name,
            x=list(range(len(dates))), y=predicted, mode="lines", line=dict(color=color, width=line_width), fill="none"
            ),
        
    )

    xaxis = go.layout.XAxis(title="Hour of day")
    yaxis = go.layout.YAxis(
        rangemode="normal",
        title=go.layout.yaxis.Title(text=f"Seasonality: {comp_name}"),
        zerolinecolor=zeroline_color,
    )

    if multiplicative:
        yaxis.update(tickformat="%", hoverformat=".2%")

    return {"traces": traces, "xaxis": xaxis, "yaxis": yaxis}


def plot_custom_season(m, comp_name, quantile=None, multiplicative=False):
    """Plot any seasonal component of the forecast.

    Args:
        m (NeuralProphet): fitted model.
        comp_name (str): Name of seasonality component.
        multiplicative (bool): set y axis as percentage
    Returns:
        A dictionary with Plotly traces, xaxis and yaxis
    """

    traces = []
    color = "#0072B2"
    line_width = 1
    zeroline_color = "#AAA"

    t_i, predicted = predict_one_season(m, name=comp_name, n_steps=300, quantile=quantile)
    traces = []

    traces.append(
        go.Scatter(
            name=comp_name,
            x=range(t_i), y=predicted, mode="lines", line=dict(color=color, width=line_width), fill="none"),
        
    )

    xaxis = go.layout.XAxis(title=f"One period: {comp_name}")
    yaxis = go.layout.YAxis(
        rangemode="normal",
        title=go.layout.yaxis.Title(text=f"Seasonality: {comp_name}"),
        zerolinecolor=zeroline_color,
    )

    if multiplicative:
        yaxis.update(tickformat="%", hoverformat=".2%")

    return {"traces": traces, "xaxis": xaxis, "yaxis": yaxis}


def plot_parameters_plotly(m, quantile=None, forecast_in_focus=None, weekly_start=0, yearly_start=0, figsize=(900, 200)):
    """Plot the parameters that the model is composed of, visually.

    Args:
        m (NeuralProphet): fitted model.
        forecast_in_focus (int): n-th step ahead forecast AR-coefficients to plot
        quantile (float): the quantile for which the model parameters are to be plotted
        weekly_start (int):  specifying the start day of the weekly seasonality plot.
            0 (default) starts the week on Sunday.
            1 shifts by 1 day to Monday, and so on.
        yearly_start (int): specifying the start day of the yearly seasonality plot.
            0 (default) starts the year on Jan 1.
            1 shifts by 1 day to Jan 2, and so on.
        figsize (tuple): width, height in inches.
            None (default):  automatic (10, 3 * npanel)

    Returns:
        A plotly figure.
    """
    quantile_index = m.model.quantiles.index(quantile)
    parameter_components = get_parameter_components(m, forecast_in_focus,quantile)

    components = parameter_components["components"]
    additive_future_regressors = parameter_components["additive_future_regressors"]
    additive_events = parameter_components["additive_events"]
    multiplicative_future_regressors = parameter_components["multiplicative_future_regressors"]
    multiplicative_events = parameter_components["multiplicative_events"]

    npanel = len(components)
    figsize = figsize if figsize else (10, 3 * npanel)

    # Create Plotly subplot figure and add the components to it
    fig = make_subplots(npanel, cols=1, print_grid=False)
    fig["layout"].update(go.Layout(showlegend=False, width=figsize[0], height=figsize[1] * npanel))

    if npanel == 1:
        axes = [axes]

    for i, comp in enumerate(components):
        is_multiplicative = False
        plot_name = comp["plot_name"].lower()
        if plot_name.startswith("trend"):
            if "change" in plot_name:
                trace_object = plot_trend_change(m, quantile=quantile, plot_name=comp["plot_name"])
            else:
                trace_object = plot_trend(m,quantile=quantile, plot_name=comp["plot_name"])

        elif plot_name.startswith("seasonality"):
            name = comp["comp_name"]
            if m.season_config.mode == "multiplicative":
                is_multiplicative = True
            if name.lower() == "weekly" or m.season_config.periods[name].period == 7:
                trace_object = plot_weekly(
                    m=m, quantile=quantile,weekly_start=weekly_start, comp_name=name, multiplicative=is_multiplicative
                )
            elif name.lower() == "yearly" or m.season_config.periods[name].period == 365.25:
                trace_object = plot_yearly(
                    m=m, quantile=quantile,yearly_start=yearly_start, comp_name=name, multiplicative=is_multiplicative
                )
            elif name.lower() == "daily" or m.season_config.periods[name].period == 1:
                trace_object = plot_daily(m=m, quantile=quantile,comp_name=name, multiplicative=is_multiplicative)
            else:
                trace_object = plot_custom_season(m=m, quantile=quantile, comp_name=name, multiplicative=is_multiplicative)

        elif  "lagged weights" in plot_name:
            print(comp["focus"])
            trace_object = plot_lagged_weights(
                weights=comp["weights"], comp_name=comp["comp_name"], focus=comp["focus"]
            )
            
        else:
            if "additive future regressor" in plot_name:
                weights = additive_future_regressors
            elif "multiplicative future regressor" in plot_name:
                is_multiplicative = True
                weights = multiplicative_future_regressors
            elif "lagged scalar regressor" in plot_name:
                weights = lagged_scalar_regressors
            elif "additive event" in plot_name:
                weights = additive_events
            elif "multiplicative event" in plot_name:
                is_multiplicative = True
                weights = multiplicative_events
            trace_object = plot_scalar_weights(
                weights=weights, plot_name=comp["plot_name"], focus=forecast_in_focus, multiplicative=is_multiplicative
            )

        if i == 0:
            xaxis = fig["layout"]["xaxis"]
            yaxis = fig["layout"]["yaxis"]
        else:
            xaxis = fig["layout"]["xaxis{}".format(i + 1)]
            yaxis = fig["layout"]["yaxis{}".format(i + 1)]

        xaxis.update(trace_object["xaxis"])
        yaxis.update(trace_object["yaxis"])
        for trace in trace_object["traces"]:
            fig.add_trace(trace, i + 1, 1)

    return fig
